using System.Text.Json;
using Romsg;
using Ro3Emu.Server.Accounts;
using Ro3Emu.Server.Data;
using Ro3Emu.Server.Data.Config;

namespace Ro3Emu.Server.Game;

/// <summary>
/// The GM panel ops that build a class preset's skills (GMCombinationConfig): a profession's skill build, the bar and the
/// queue. They change the current plan as 31151 / 31153 / 31159 would and publish 5025, without those replies.
/// </summary>
public sealed partial class SkillService
{
    /// <summary>
    /// GMJobProfessSkill 170: the current plan takes a profession's recommended skills at the role's job rank and level,
    /// JobProfessSkillConfig[profession * 1000 + rank * 100 + job level]._kDescData, the row the client's own
    /// recommendation reads (LC_SkillGrowthHelper: GetAddPointOneConfigByProfession with jobAdvanceLevel and jobLevel).
    /// (inferred) Without that row the nearest one below it; the plan's other skills go back to their free level as a
    /// reset would, and a skill outside the career's tree is left out.
    /// </summary>
    internal async Task<string> GmProfessionSkillsAsync(GameSession session, long profession, bool fitPoints = false)
    {
        string reply;
        bool learned = false;
        using (await CharacterService.Lock(session))
        {
            var p = session.Progress!;
            int rank = Rank(p.Career);
            var build = JobProfessSkillConfig.All(_data)
                .Where(r => r.ProfessionId == profession && (r.JobRank < rank || r.JobRank == rank && r.JobLv <= p.JobLevel))
                .OrderByDescending(r => r.JobRank).ThenByDescending(r => r.JobLv).Cast<JobProfessSkillConfig?>().FirstOrDefault();
            if (build is not { } row) return $"Profession {profession} has no skill build up to job rank {rank}.";
            var plans = Build(session);
            var selected = plans.SkillAssignPlans[plans.CurrentPlanIndex];
            foreach (var (group, assigned) in selected.SkillAssigns)
                assigned.SkillLevel = IsNormalGroup(p, group) ? 1 : Initial(p, group);
            var tree = Tree(p.Career).Select(t => t.Group).ToHashSet();
            var outside = new List<long>();
            var order = new List<int>();
            var levels = new Dictionary<int, int>();
            foreach (long id in row.DescData)
            {
                if (SkillConfig.Get(_data, id) is not { } skill || !tree.Contains((int)skill.SkillId)) { outside.Add(id); continue; }
                int group = (int)skill.SkillId;
                levels[group] = Math.Max((int)skill.Level, Initial(p, group));
                if (!order.Contains(group)) order.Add(group);
            }
            // fitPoints (a build chosen in the skill window): a build a point or two over what the job level earns is not
            // refused whole; levels are taken off its last skills until each rank fits.
            int trimmed = fitPoints ? FitToPoints(p, tree, order, levels) : 0;
            foreach (int group in order) selected.SkillAssigns[group] = new SkillData { SkillId = group, SkillLevel = levels[group] };
            var candidate = CharacterService.GenreCloneProgress(p);
            int code = StageSkillPlan(session, plans, candidate, plans.CurrentPlanIndex);
            if (code != 0) reply = $"Skill build {row.Id} refused (error {code}).";
            else
            {
                PruneLoads(selected, candidate);
                await CommitAsync(session, plans, candidate, "cd13c/skill-gm-profession");
                await SyncAsync(session);
                learned = true;
                reply = $"Skill build {row.Id}: {row.DescData.Length - outside.Count} skills"
                    + (trimmed > 0 ? $", {trimmed} levels cut to the points left" : "")
                    + (outside.Count > 0 ? $", not in this career's tree: {string.Join(' ', outside)}." : ".");
            }
        }
        if (learned) foreach (var handler in Learned) await handler(session);
        return reply;
    }

    /// <summary>
    /// Takes levels off a build's last skills until every job rank is within the points it earned, never off a skill another
    /// skill of the build needs (_kPreSkillRequired), then drops any skill whose requirement is still unmet (as
    /// PrerequisitesMet would refuse it). Returns the levels cut. (inferred) Cutting the list's own order, last first, keeps
    /// the early core skills of a build.
    /// </summary>
    private int FitToPoints(RoleProgress p, HashSet<int> tree, List<int> order, Dictionary<int, int> levels)
    {
        var rankOf = Tree(p.Career).ToDictionary(t => t.Group, t => Rank(t.Career));
        var left = EarnedPoints(p);
        foreach (int group in order)
            if (rankOf.TryGetValue(group, out int rank) && left.ContainsKey(rank)) left[rank] -= Math.Max(0, levels[group] - Initial(p, group));
        int LevelOf(int group) => levels.TryGetValue(group, out int level) ? level : Initial(p, group);
        bool NeededByOthers(int group)
        {
            foreach (int other in order)
            {
                if (other == group || levels[other] <= Initial(p, other)) continue;
                foreach (var (need, minimum) in Requirements(other, levels[other]))
                    if (need == group && minimum > 0 && LevelOf(group) - 1 < minimum) return true;
            }
            return false;
        }
        int cut = 0;
        foreach (int rank in left.Keys.ToArray())
            while (left[rank] < 0)
            {
                int pick = order.AsEnumerable().Reverse().FirstOrDefault(
                    g => rankOf.GetValueOrDefault(g) == rank && levels[g] > Initial(p, g) && !NeededByOthers(g), 0);
                if (pick == 0) break;
                levels[pick]--; left[rank]++; cut++;
            }
        bool again = true;
        while (again)
        {
            again = false;
            foreach (int group in order)
            {
                if (levels[group] <= Initial(p, group)) continue;
                // As PrerequisitesMet: a minimum level (> 0) must be reached, a -1 skill must not be held.
                bool met = Requirements(group, levels[group]).All(r =>
                {
                    int actual = tree.Contains(r.Group) ? LevelOf(r.Group) : 0;
                    return !(r.Minimum == -1 ? actual > 0 : r.Minimum > 0 && actual < r.Minimum);
                });
                if (met) continue;
                levels[group] = Initial(p, group);
                again = true;
            }
        }
        return cut;
    }

    /// <summary>SkillConfig._kPreSkillRequired of a skill level: (group, minimum level) pairs, -1 meaning the skill must not be held.</summary>
    private IEnumerable<(int Group, long Minimum)> Requirements(int group, int level)
    {
        if (_data.Row("SkillConfig", RealId(group, level)) is not { } row
            || !row.TryGetProperty("_kPreSkillRequired", out var pre) || pre.ValueKind != JsonValueKind.Array) yield break;
        foreach (var req in pre.EnumerateArray())
            if (req.ValueKind == JsonValueKind.Array && req.GetArrayLength() >= 2 && GameData.Number(req[0]) is { } g
                && GameData.Number(req[1]) is { } l)
                yield return ((int)g, (long)l);
    }

    /// <summary>
    /// GMSetSkillWheel 172: the bar from SkillConfig row ids, slot n = the n-th id (ManualLoadSkill, as 31153 fills it),
    /// 0 an empty slot. A normal attack row (the presets name JobAttackConfig's, 1001001 for a Swordman) is the career's
    /// own, as a saved bar shows it (BarGroup). (inferred) A skill the role has not learned, a passive or a repeat leaves
    /// its slot empty; a level above the learned one is lowered to it.
    /// </summary>
    internal async Task<string> GmSkillBarAsync(GameSession session, IReadOnlyList<long> rows)
    {
        using var held = await CharacterService.Lock(session);
        var p = session.Progress!;
        var plans = Build(session);
        var selected = plans.SkillAssignPlans[plans.CurrentPlanIndex];
        selected.ManualLoadSkill.Clear();
        var empty = new List<long>();
        foreach (long id in rows.Take(BarSlots))
        {
            var skill = SkillConfig.Get(_data, id);
            int group = skill is not { } s ? 0 : s.IsNormalAtk == 1 ? CareerNormalGroup(p.Career) : (int)s.SkillId;
            int learned = group > 0 ? Level(p, group) : 0;
            bool fits = learned > 0 && IsActive(group) && selected.ManualLoadSkill.All(load => load.SkillId != group);
            if (!fits && id != 0) empty.Add(id);
            selected.ManualLoadSkill.Add(fits ? new SkillData { SkillId = group, SkillLevel = Math.Min(learned, (int)skill!.Value.Level) } : new SkillData());
        }
        if (selected.ManualLoadSkill.Count == 0) selected.ManualLoadSkill.Add(new SkillData());
        var candidate = CharacterService.GenreCloneProgress(p);
        int code = StageSkillPlan(session, plans, candidate, plans.CurrentPlanIndex);
        if (code != 0) return $"Skill bar refused (error {code}).";
        await CommitAsync(session, plans, candidate, "cd13c/skill-gm-bar");
        await SyncAsync(session);
        empty.AddRange(rows.Skip(BarSlots).Where(id => id != 0));
        int placed = selected.ManualLoadSkill.Count(load => load.SkillId > 0);
        return $"Skill bar: {placed} skills" + (empty.Count > 0 ? $", left out (not learned, passive or no slot): {string.Join(' ', empty)}." : ".");
    }

    /// <summary>
    /// GMSkillQueue 38: the current plan's queue in the given order (skill group ids, queue index 0 first) at the learned
    /// levels, as 31159 fills it one index at a time. (inferred) It replaces the whole queue; a skill not learned or not
    /// queueable (SkillConfig._iQueue, or the combat override) is left out.
    /// </summary>
    internal async Task<string> GmSkillQueueAsync(GameSession session, IReadOnlyList<int> groups)
    {
        using var held = await CharacterService.Lock(session);
        var p = session.Progress!;
        var plans = Build(session);
        var queue = plans.SkillAssignPlans[plans.CurrentPlanIndex].SkillQueue;
        queue.Clear();
        int capacity = Parameter("QueueNum", 12);
        var skipped = new List<int>();
        foreach (int group in groups)
        {
            int learned = Level(p, group);
            bool queueable = SkillConfig.Get(_data, RealId(group, 1)) is { } row && (QueueEligibility?.Invoke(session, group) ?? row.Queue == 1);
            if (learned <= 0 || !queueable || queue.Count >= capacity || queue.Values.Any(q => q.SkillId == group)) { skipped.Add(group); continue; }
            queue[queue.Count] = new SkillData { SkillId = group, SkillLevel = learned };
        }
        if (queue.Count == 0) queue[0] = new SkillData(); // the client pairs() it unguarded
        await CommitAsync(session, plans, kind: "cd13c/skill-gm-queue");
        await SyncAsync(session);
        return $"Skill queue: {groups.Count - skipped.Count} skills"
            + (skipped.Count > 0 ? $", left out (not learned or not queueable): {string.Join(' ', skipped)}." : ".");
    }
}
