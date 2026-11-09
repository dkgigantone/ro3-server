using Romsg;
using Ro3Emu.Server.Maps;

namespace Ro3Emu.Server.Game;

/// <summary>
/// Explosions on the airship deck (ScriptedStep.Booms / Response.Booms; inferred, see <see cref="ReconstructedDungeons.Booms"/>):
/// the original's clips show red and black blast marks appear on the deck while the enemy ships fire and again after the
/// captain is spoken to. Each shot spawns the invisible NPC 5300037 where the scene editor puts the effect, plays its skill
/// 5353101 from there to every player and removes it again. The enemy ship that "fires" first plays its own skill 5353201
/// towards the same point. Visual only. Also the GM test aids !chatline and !boom.
/// </summary>
public sealed partial class DungeonService
{
    private static long _boomSession = 8_000_000_000L;
    private const int BoomLeadMs = 700, BoomLifeMs = 3000;
    /// <summary>Test aid !boomfx: the skills every deck explosion plays instead of its scene's own (null: the scene's).</summary>
    private long[]? _boomSkills;
    private bool _voiceOn = true;

    /// <summary>
    /// (inferred) The voiced rows of the older airship script (ChatConfig 4380035-45, _iAudio VO_Guide_Airship_01-11) and the
    /// portrait rows (5300540-68, no sound) that say the same line, paired by their English text.
    /// </summary>
    private static readonly IReadOnlyDictionary<int, int> VoiceTwins = new Dictionary<int, int>
    {
        [5300540] = 4380035, [5300550] = 4380036, [5300552] = 4380037, [5300553] = 4380038, [5300554] = 4380039, [5300557] = 4380040,
        [5300558] = 4380041, [5300561] = 4380042, [5300559] = 4380043, [5300566] = 4380044, [5300568] = 4380045,
    };

    private void StartBooms(Attempt a, int generation, ReconstructedDungeons.Booms booms, bool persist = false)
    {
        foreach (var shot in booms.Shots)
            StepLater(a, generation, shot.AtMs, () => BoomAsync(a, booms, shot.Point), persist || booms.Persist);
    }

    private async Task BoomAsync(Attempt a, ReconstructedDungeons.Booms booms, long point)
    {
        if (a.Settled || a.Room.Closed) return;
        if (_catalog.ScenePoint(a.Plan.SceneEntry, point) is not { } at)
        {
            _log.LogWarning("Scripted scene {Scene}: boom point {Point} is not in the scene", a.Plan.SceneEntry, point);
            return;
        }
        var target = new Vec3 { X = at.X, Y = at.Y, Z = at.Z };
        if (booms.Shooter != 0 && booms.ShooterSkill != 0
            && a.Room.Monsters.FirstOrDefault(m => m.Template.NpcId == booms.Shooter && !m.Dead && m.Hp > 0) is { } shooter)
            await _combat.SendMonsterTimelineAsync(shooter, new UseInSkillInfo
            {
                SrcID = shooter.Uid, SkillID = (int)booms.ShooterSkill, TargetId = 0, TargetPos = target.Clone(), Face = shooter.Face,
                SessionId = Interlocked.Increment(ref _boomSession), ChantMs = 0, SkillMs = 200, StartTime = MonsterService.NowMs, PlayAct = true,
            });
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(booms.Shooter != 0 ? BoomLeadMs : 1), () => ExplodeAsync(a, booms.Npc, booms.Skill, at, target), "dungeon deck boom");
    }

    private async Task ExplodeAsync(Attempt a, int npc, long skill, MapPoint at, Vec3 target, long[]? only = null)
    {
        if (a.Settled || a.Room.Closed) return;
        var caster = await _instances.SpawnAsync(a.Room, npc, new MapPoint(at.X, at.Y, at.Z, 0), grounded: false);
        _log.LogInformation("Scripted scene {Scene}: explosion at {X:0.0},{Z:0.0} (skill {Skill})", a.Plan.SceneEntry, at.X, at.Z, skill);
        if (caster == null)
        {
            _log.LogWarning("Scripted scene {Scene}: boom caster {Npc} could not be spawned", a.Plan.SceneEntry, npc);
            return;
        }
        caster.StaysOutOfBattle = true;
        caster.KeepsDamage = true;
        await _world.ShowFromAfarAsync(caster);
        // The deck explosion burns the ground (5353101) and shakes the screen (5353201, confirmed by !boomfx).
        long[] played = [skill];
        if (only is { Length: > 0 }) played = only;
        else if (_boomSkills is { Length: > 0 } custom) played = custom;
        else if (skill == 5353101) played = [skill, 5353201];
        foreach (long id in played)
            await _combat.SendMonsterTimelineAsync(caster, new UseInSkillInfo
            {
                SrcID = caster.Uid, SkillID = (int)id, TargetId = 0, TargetPos = target.Clone(), Face = 0,
                SessionId = Interlocked.Increment(ref _boomSession), ChantMs = 0, SkillMs = 200, StartTime = MonsterService.NowMs, PlayAct = true,
            });
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(BoomLifeMs), () => _instances.RemoveMonsterAsync(a.Room, caster), "dungeon deck boom end");
        // The scorch the clips show after a deck explosion: the same burning ground the cannon hits leave.
        if (skill == 5353101 && only == null && _boomSkills == null) await BurnAsync(a, target);
    }

    /// <summary>!chatline: sends a ChatConfig row to the caller the way a scripted step's lines go out (a 1022).</summary>
    private async Task ChatLineGmAsync(GameSession s, string[] args)
    {
        if (args.Length == 0) throw new ArgumentException("<chatConfigId>");
        int chat = int.Parse(args[0]);
        if (_data.Row("ChatConfig", chat) == null) throw new ArgumentException($"ChatConfig row {chat} does not exist");
        await s.SendGameAsync(SystemBroadcast, new SystemBoardCastMsgNtf { Source = MsgSource.Npc, ChatId = chat, PlayerId = 0 });
        await GmCommands.ReplyAsync(s, $"Sent ChatConfig {chat} (test aid).");
    }

    /// <summary>!boom: plays one deck explosion at a scene-editor point of the scene the caller is in.</summary>
    private async Task BoomGmAsync(GameSession s, string[] args)
    {
        if (Active(s) is not { } a) throw new ArgumentException("only inside a dungeon scene");
        long point = args.Length > 0 ? long.Parse(args[0]) : 59;
        int npc = args.Length > 1 ? int.Parse(args[1]) : 5300037;
        long skill = args.Length > 2 ? long.Parse(args[2]) : 5353101;
        await BoomAsync(a, new ReconstructedDungeons.Booms(npc, skill, 0, 0, true, []), point);
        await GmCommands.ReplyAsync(s, $"Boom at point {point} with NPC {npc}, skill {skill} (test aid).");
    }

    /// <summary>!voice: switches the voiced twin of each scripted line on or off.</summary>
    private async Task VoiceGmAsync(GameSession s, string[] args)
    {
        if (args.Length > 0) _voiceOn = !args[0].Equals("off", StringComparison.OrdinalIgnoreCase);
        await GmCommands.ReplyAsync(s, $"Voiced twin lines are {(_voiceOn ? "on" : "off")} (test aid).");
    }

    /// <summary>!boomfx: the skills the deck explosions play (comma separated), or "off" for the scene's own.</summary>
    private async Task BoomFxGmAsync(GameSession s, string[] args)
    {
        if (args.Length == 0) throw new ArgumentException("<skill[,skill..]|off>");
        _boomSkills = args[0].Equals("off", StringComparison.OrdinalIgnoreCase) ? null : [.. args[0].Split(',').Select(long.Parse)];
        await GmCommands.ReplyAsync(s, $"Deck explosions play {(_boomSkills == null ? "the scene's skill" : string.Join(',', _boomSkills))} (test aid, until the server restarts).");
    }

    /// <summary>!plot: plays a PlotInterpretation story for the caller, as a step's story does (the var-change event).</summary>
    private async Task PlotGmAsync(GameSession s, string[] args)
    {
        if (args.Length == 0) throw new ArgumentException("<plotInterpretationId>");
        long plot = long.Parse(args[0]);
        if (_data.Row("PlotInterpretation", plot) == null) throw new ArgumentException($"PlotInterpretation row {plot} does not exist");
        await s.SendGameAsync(Event, new DungeonEventNotify
        {
            DungeonEntry = s.SceneEntry, EventType = (DungeonEventType)4, StageNum = 1, LevelNum = 1,
            Vars = { new DungeonVar { VarId = StoryVar, Vi64 = { plot } } },
        });
        await GmCommands.ReplyAsync(s, $"Plot {plot} sent (test aid).");
    }
}
