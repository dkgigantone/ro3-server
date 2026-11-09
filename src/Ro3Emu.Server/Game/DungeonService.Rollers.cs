using Romsg;
using Ro3Emu.Server.Maps;

namespace Ro3Emu.Server.Game;

/// <summary>
/// Barrels rolling across the airship deck during the Evil Boss fight (ScriptedStep.Rollers; the whole mechanic is inferred:
/// the scene editor has the barrel start points and lane patrols, the NPCs have no script). Each round spawns the barrel NPCs
/// on their lanes, flies them like the enemy ships (<see cref="FlyAsync"/>), hurts participants they touch and removes them
/// at the end of their lane. The barrels are not counted as the fight's monsters, so the step never waits for them.
/// </summary>
public sealed partial class DungeonService
{
    private sealed record Roll(MonsterUnit Unit, IReadOnlyList<(float X, float Y, float Z)> Route, float Speed, long StartMs, long EndMs);

    private void StartRollers(Attempt a, ReconstructedDungeons.Rollers rollers)
    {
        int generation = a.StepGeneration;
        if (rollers.BelowPct > 0)
            StepLater(a, generation, WatchMs, () => WatchBossAsync(a, generation, rollers), whileFighting: true);
        else
            StepLater(a, generation, rollers.FirstMs, () => RollRoundAsync(a, generation, rollers, repeat: true), whileFighting: true);
    }

    private const int WatchMs = 500;

    /// <summary>The single-phase barrels (Rollers.BelowPct): once the boss is at or below that percent of its health, the
    /// phase's rounds start, one every WaveMs.</summary>
    private async Task WatchBossAsync(Attempt a, int generation, ReconstructedDungeons.Rollers rollers)
    {
        var boss = a.Room.Monsters.FirstOrDefault(m => m.Template.NpcId == rollers.Boss && !m.Dead && m.Hp > 0);
        if (boss == null) return;
        if (!a.HugeFire && boss.Hp * 100 <= boss.Template.MaxHp * 20)
        {
            a.HugeFire = true;
            await HugeFireAsync(a, boss, 5);
        }
        if (boss.Hp * 100 <= boss.Template.MaxHp * rollers.BelowPct && !a.Rolling)
        {
            a.Rolling = true;
            _log.LogInformation("Scripted scene {Scene}: the boss is at {Hp}/{Max}, the barrel phase starts",
                a.Plan.SceneEntry, boss.Hp, boss.Template.MaxHp);
            // The rounds follow one another: the next starts WaveMs after the previous round's barrels have gone.
            StepLater(a, generation, 1, () => RollRoundAsync(a, generation, rollers, repeat: false, more: Math.Max(1, rollers.Waves) - 1), whileFighting: true);
        }
        if (a.HugeFire && a.Rolling) return;
        StepLater(a, generation, WatchMs, () => WatchBossAsync(a, generation, rollers), whileFighting: true);
        await Task.CompletedTask;
    }

    private async Task RollRoundAsync(Attempt a, int generation, ReconstructedDungeons.Rollers rollers, bool repeat, int more = 0)
    {
        if (a.Room.Monsters.FirstOrDefault(m => m.Template.NpcId == rollers.Boss && !m.Dead && m.Hp > 0) == null) return;
        // The next round is due from this one's start.
        if (repeat) StepLater(a, generation, rollers.EveryMs, () => RollRoundAsync(a, generation, rollers, repeat: true), whileFighting: true);
        _log.LogInformation("Scripted scene {Scene}: barrels roll", a.Plan.SceneEntry);
        if (rollers.Tip != 0)
            await BroadcastAsync(a, SystemBroadcast, new SystemBoardCastMsgNtf { Source = MsgSource.System, ChatId = rollers.Tip });
        var rolls = new List<Roll>();
        for (int i = 0; i < rollers.Npcs.Count; i++)
        {
            if (a.Settled || a.Room.Closed) break;
            if (_catalog.ScenePatrol(a.Plan.SceneEntry, rollers.Routes[i]) is not { Count: > 1 } route) continue;
            var unit = await _instances.SpawnAsync(a.Room, rollers.Npcs[i], new MapPoint(route[0].X, route[0].Y, route[0].Z, 0), grounded: false);
            if (unit == null) { _log.LogWarning("Scripted scene {Scene}: barrel {Npc} could not be spawned", a.Plan.SceneEntry, rollers.Npcs[i]); continue; }
            float speed = unit.Template.SpeedCm / 100f;
            if (speed <= 0) { await _instances.RemoveMonsterAsync(a.Room, unit); continue; }
            long now = MonsterService.NowMs;
            rolls.Add(new Roll(unit, route, speed, now, now + (long)(RouteLength(route) / speed * 1000)));
            await FlyAsync(a, unit, route);
        }
        if (rolls.Count == 0) return;
        long end = rolls.Max(r => r.EndMs);
        await BarrelFxStartAsync(a, end, [.. rolls.Select(r => r.Route)]);
        if (more > 0)
            StepLater(a, generation, (int)Math.Max(1, end - MonsterService.NowMs) + 500 + rollers.WaveMs, () => RollRoundAsync(a, generation, rollers, repeat: false, more: more - 1), whileFighting: true);
        var lastHit = new Dictionary<long, long>();
        StepLater(a, generation, 250, () => RollHitAsync(a, generation, rollers, rolls, lastHit), whileFighting: true);
        // Removal does not depend on the step: the barrels go when their lanes end, whatever happened to the fight.
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(Math.Max(1, end - MonsterService.NowMs) + 500), async () =>
        {
            foreach (var roll in rolls) await _instances.RemoveMonsterAsync(a.Room, roll.Unit);
        }, "dungeon barrels");
    }

    private async Task RollHitAsync(Attempt a, int generation, ReconstructedDungeons.Rollers rollers, List<Roll> rolls, Dictionary<long, long> lastHit)
    {
        long now = MonsterService.NowMs;
        if (a.Settled || a.Room.Closed) return;
        var boss = a.Room.Monsters.FirstOrDefault(m => m.Template.NpcId == rollers.Boss && !m.Dead && m.Hp > 0);
        var live = rolls.Where(r => now < r.EndMs && !r.Unit.Dead).ToArray();
        if (boss == null || live.Length == 0) return;
        if (_data.Row("SkillConfig", rollers.Skill) is { } row)
        {
            var hit = new List<GameSession>();
            foreach (var m in Present(a).Where(m => a.Loaded.Contains(m) && !m.SceneLoading && !m.Dead))
            {
                long id = m.Role!.Id;
                if (lastHit.TryGetValue(id, out var at) && now - at < rollers.HitEveryMs) continue;
                if (live.Any(r => RollPosition(r, now) is var (x, z)
                        && MonsterService.Distance(m.Position.X, m.Position.Z, x, z) <= rollers.Radius))
                {
                    lastHit[id] = now;
                    hit.Add(m);
                }
            }
            if (hit.Count > 0) await _combat.MonsterSkillHitAsync(boss, row, [.. hit], boss.Uid);
        }
        StepLater(a, generation, 250, () => RollHitAsync(a, generation, rollers, rolls, lastHit), whileFighting: true);
    }

    private static float RouteLength(IReadOnlyList<(float X, float Y, float Z)> route)
    {
        float sum = 0;
        for (int i = 1; i < route.Count; i++) sum += MonsterService.Distance(route[i - 1].X, route[i - 1].Z, route[i].X, route[i].Z);
        return sum;
    }

    /// <summary>Where a barrel is now: its speed along its lane since it started (FlyLegAsync moves the server unit to each
    /// leg's end at once, so the unit's own position is no use).</summary>
    private static (float X, float Z) RollPosition(Roll roll, long now)
    {
        float left = Math.Max(0, (now - roll.StartMs) / 1000f * roll.Speed);
        for (int i = 1; i < roll.Route.Count; i++)
        {
            var (from, to) = (roll.Route[i - 1], roll.Route[i]);
            float leg = MonsterService.Distance(from.X, from.Z, to.X, to.Z);
            if (left <= leg || i == roll.Route.Count - 1)
            {
                float t = leg <= 0 ? 1 : Math.Min(1, left / leg);
                return (from.X + (to.X - from.X) * t, from.Z + (to.Z - from.Z) * t);
            }
            left -= leg;
        }
        return (roll.Route[^1].X, roll.Route[^1].Z);
    }
}
