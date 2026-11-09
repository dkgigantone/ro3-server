using System.Globalization;
using System.Text.Json;

namespace Ro3Emu.Server.Game;

/// <summary>
/// Scripted story scenes, with or without fights (Reconstructed/scripted-scenes.json; its "contract" section cites the client
/// code for every event used here). The original server's scene script is not in the client: each scene is rebuilt as
/// steps, each naming the DungeonQuestConfig quest the tracker shows, the scene actors shown during it, an optional
/// run-time story the client plays, and the client events that complete it. Nothing is keyed by a task id: a step
/// completes on any listed event (interaction, dungeon message, region, point or plot end).
/// </summary>
public sealed partial class ReconstructedDungeons
{
    public enum TriggerKind { Interaction, Message, Region, Point, Plot }

    /// <summary>A client event that completes (or, under "ignore", deliberately does not complete) a step.</summary>
    public sealed record StepTrigger(TriggerKind Kind, long Id, float Radius = 0);

    /// <summary>A scene actor: an NPCConfig id at a scene-editor point, with the buffs (BuffConfig ids) it carries.
    /// <paramref name="Dx"/>/<paramref name="Dz"/> (metres) place it beside the point when the scene has no point of its own
    /// for it (inferred placement).</summary>
    public sealed record SceneActor(int NpcId, long Point, IReadOnlyList<int> Buffs, float Dx = 0, float Dz = 0);

    /// <summary>
    /// One scripted step. <paramref name="Story"/> is a PlotInterpretation id the client plays at the step's start
    /// (0: none); <paramref name="TeleportAfter"/> a scene-editor point the participants are moved to when it
    /// completes (0: none). <paramref name="Goal"/> is the tracker's "(done/total)" while the step runs.
    /// <paramref name="Fight"/> are monsters spawned at the step's start: the step also waits until every one is
    /// defeated (a story dungeon that mixes fights with scripted parts). <paramref name="All"/>: every completing event
    /// is needed, not any one ("Have a Chitchat with them"). <paramref name="Cannon"/>: an actor's interaction seats its
    /// user on a cannon of the client's own gameplay, which fires its skill at the step's fight (DungeonService.Cannon).
    /// <paramref name="Seek"/>: the step is a hide-and-seek game
    /// whose hiding places are its Point triggers; walking there does not complete it, winning the game does.
    /// <paramref name="Companions"/>: the actors marked "companion", shown as allied fighters (CompanionUnit) instead of
    /// plain scene actors; they are not in <paramref name="Actors"/>.
    /// <paramref name="React"/>: what the step's speaker says, and the effect shown, when it begins, when an ignored event
    /// arrives and when it completes.
    /// </summary>
    public sealed record ScriptedStep(string Stage, int Quest, (int Done, int Total) Goal, IReadOnlyList<SceneActor> Actors,
        long Story, IReadOnlyList<StepTrigger> Complete, IReadOnlyList<StepTrigger> Ignore, long TeleportAfter,
        IReadOnlyList<Spawn> Fight, bool All, Cannon? Cannon = null,
        Seek? Seek = null, Shelter? Shelter = null, Reaction? React = null)
    {
        public IReadOnlyList<SceneActor> Companions { get; init; } = [];

        /// <summary>A scene-editor guide route (m_kGuidePoints) the actor a player used to complete the step walks, the
        /// participants with it, before <see cref="TeleportAfter"/> and the next step (0: none; inferred).</summary>
        public long RideAfter { get; init; }

        /// <summary>A scene-editor region the seated player drives the ride's carrier to after its route, held in the seat
        /// until it is reached; 0: the ride ends where its route ends (inferred, play-tested).</summary>
        public long DriveAfter { get; init; }

        /// <summary>The ride's carrier walks its route alone: the participants stay where they stand instead of going with it
        /// (inferred, play-tested).</summary>
        public bool RideAlone { get; init; }

        /// <summary>The ride's carrier speed in percent of its own (UnitConfig._fSpeed), for its route and the drive (inferred, play-tested).</summary>
        public int RideSpeedPercent { get; init; } = 100;

        /// <summary>Milliseconds the step stays on after it is completed, before the next step or the settlement (0: none;
        /// inferred): a bubble of the actors it showed is still being read, or a defeated enemy is still falling.</summary>
        public int HoldAfterMs { get; init; }

        /// <summary>The scene editor guide routes (m_kGuidePoints) shown while the step runs (DungeonService.GuideWays); null: the
        /// scene does not name them per step.</summary>
        public IReadOnlyList<long>? Guides { get; init; }

        /// <summary>The client's steerable slide while the step runs (DungeonService.Slide; null: none).</summary>
        public Slide? Slide { get; init; }

        /// <summary>Barrels rolling during the step's boss fight (DungeonService.Rollers; null: none).</summary>
        public Rollers? Rollers { get; init; }

        /// <summary>Explosions on the deck from the step's start (DungeonService.Booms; null: none).</summary>
        public Booms? Booms { get; init; }

        /// <summary>The step does not advance before this many milliseconds after it began (0: no floor): the scene's
        /// scripted lines run out first (inferred from the clips: the cannons appear after the opening script).</summary>
        public int HoldUntilMs { get; init; }
    }

    /// <summary>
    /// A slide (inferred speeds): the client moves the player downhill at <paramref name="DownCm"/> cm/s and steers it sideways at
    /// <paramref name="SideCm"/> cm/s, through the Camera row <paramref name="Camera"/> (0: the client's own). The player
    /// carries the BuffConfig row <paramref name="Buff"/> (0: none) from its start. When the step completes the slide stops
    /// and the player plays the SkillConfig row <paramref name="EndSkill"/>'s animation (0: none).
    /// </summary>
    public sealed record Slide(int SideCm, int DownCm, int EndSkill, int Camera = 0, int Buff = 0);

    /// <summary>A step's cannons: the actor's <paramref name="Interaction"/> seats its user, who fires the skill <paramref name="Fire"/>.</summary>
    public sealed record Cannon(long Interaction, int Fire);

    /// <summary>
    /// A step's reactions (inferred, Louie's 3120703 shell game: his lines and the right/wrong Bell effects are client rows
    /// that nothing in the client sends). <paramref name="Speaker"/> is the step's actor whose uid the lines carry;
    /// <paramref name="Start"/> ChatConfig rows go out one after another when the step begins, each when the previous one's
    /// _iTime has run out. <paramref name="Ignored"/> answers an event the step ignores, <paramref name="Complete"/> the
    /// event that completes it.
    /// </summary>
    public sealed record Reaction(int Speaker, IReadOnlyList<int> Start, Response? Ignored, Response? Complete)
    {
        /// <summary>Lines that go out at fixed times from the step's start, each by its own speaker (null: none). Unlike
        /// <see cref="Start"/> they do not wait for the previous line (measured from the original's clip, inferred).</summary>
        public IReadOnlyList<TimedLine>? Timed { get; init; }
    }

    /// <summary>One <see cref="Reaction.Timed"/> line: the ChatConfig row <paramref name="Say"/> goes out <paramref name="AtMs"/>
    /// after the step starts, carrying the uid of the step's <paramref name="Speaker"/> actor (0: none).</summary>
    public sealed record TimedLine(int AtMs, int Say, int Speaker);

    /// <summary>
    /// One reaction: the ChatConfig row <paramref name="Say"/> (0: none) and the BuffConfig row <paramref name="Buff"/>
    /// (0: none) put on the step's <paramref name="On"/> actor that stands at the point of the actor the player used. A
    /// completing reaction holds the next step (or the settlement) until its line's _iTime has run out.
    /// </summary>
    public sealed record Response(int Say, int Buff, int On)
    {
        /// <summary>Explosions that start when the event happens (null: none).</summary>
        public Booms? Booms { get; init; }

        /// <summary>Milliseconds the line waits after the event before it goes out (0: at once); the completing step holds
        /// that long too (inferred: the explosions after talking to the captain come first).</summary>
        public int DelayMs { get; init; }

        /// <summary>A PlotInterpretation id the client plays after the line (0: none; the ship's crash video 130161), and how
        /// long the completing step holds for it (the video's length is not in the config; inferred).</summary>
        public long Story { get; init; }
        public int StoryHoldMs { get; init; }
        /// <summary>Plots that follow the story by themselves (the crash video's QTE plays 130162 or 130163); the step holds until
        /// one of them ends instead of when the story itself does (empty: the story's own end).</summary>
        public IReadOnlyList<long> StoryThen { get; init; } = [];
    }

    /// <summary>
    /// Cover from a boss's sweeping attack (inferred, the story Pharaoh's 3120702 after the raid Pharaoh's "Pharaoh is
    /// unleashing Cleansing Light! Quickly, hide behind the Pyramid!"): every <paramref name="EveryMs"/> while the fight's
    /// <paramref name="Boss"/> lives, the <paramref name="Tip"/> ChatConfig row goes out and the boss holds its ground for
    /// <paramref name="WarnMs"/>; then <paramref name="Skill"/> hits every participant not in the shadow of the
    /// <paramref name="Cover"/> actor: behind it as seen from the boss, within <paramref name="Width"/> metres of that line
    /// and <paramref name="Depth"/> metres past the cover.
    /// </summary>
    public sealed record Shelter(int Boss, int Cover, int Tip, long Skill, int EveryMs, int WarnMs, float Width, float Depth);

    /// <summary>
    /// Barrels rolling across the deck while a fight's <paramref name="Boss"/> lives (inferred, the airship's Evil Boss 4: the
    /// scene editor has barrel start points 32-35 and lane patrols 69-72, the barrel NPCs 5300020/23/24/25 and no script). From
    /// <paramref name="FirstMs"/>, then every <paramref name="EveryMs"/>, each NPC of <paramref name="Npcs"/> rolls the patrol at
    /// the same index of <paramref name="Routes"/> at its own speed and is removed at the end. A participant within
    /// <paramref name="Radius"/> metres of a barrel is hit by <paramref name="Skill"/> (the boss as its source), at most once
    /// per <see cref="HitEveryMs"/>. <paramref name="Tip"/> is an optional ChatConfig row sent when a roll starts (0: none).
    /// </summary>
    /// <summary>One explosion: <paramref name="AtMs"/> after the start, at the scene-editor point <paramref name="Point"/>.</summary>
    public sealed record Boom(int AtMs, long Point);

    /// <summary>
    /// Explosions on the deck (inferred: the scene editor's "敌方火炮特效位置" points 59/60 and "火炮特效标记" 31 are where the enemy
    /// cannons' effects land, and the invisible NPC 5300037 owns skill 5353101, range 99 km, 200 ms, with no hit data): each
    /// shot spawns <paramref name="Npc"/> at its point for a moment and plays <paramref name="Skill"/> from it at that point
    /// to every player. When <paramref name="Shooter"/> (an NPC of the room, 0: none) is alive it plays
    /// <paramref name="ShooterSkill"/> at the point first. <paramref name="Persist"/>: the shots go on after the step is done.
    /// The effect only; it hurts nobody.
    /// </summary>
    public sealed record Booms(int Npc, long Skill, int Shooter, long ShooterSkill, bool Persist, IReadOnlyList<Boom> Shots);

    public sealed record Rollers(int Boss, IReadOnlyList<int> Npcs, IReadOnlyList<long> Routes, long Skill, int FirstMs, int EveryMs,
        float Radius, int Tip, int HitEveryMs)
    {
        /// <summary>When above 0 the barrels roll once, as a single phase, from the moment the boss is at or below this percent of
        /// its health (inferred from the original's clip: about 8 s of barrels at low boss health); <see cref="FirstMs"/> and
        /// <see cref="EveryMs"/> then do not apply. 0: the periodic rounds.</summary>
        public int BelowPct { get; init; }

        /// <summary>The phase's rounds (each rolls every NPC once) and the gap between them in milliseconds.</summary>
        public int Waves { get; init; } = 1;
        public int WaveMs { get; init; }
    }

    /// <summary>
    /// A hide-and-seek game (inferred, Moonlight Flower's 3120802): the step's first actor opens with its
    /// <paramref name="Opening"/> bubbles; each round warns (<paramref name="Round"/>), counts down
    /// (<paramref name="Countdown"/>, a CombatCountdownTip row whose time is the count) and looks. Every participant must
    /// then stand at one of the step's points that this game has not searched yet. A found participant restarts the game
    /// (<paramref name="Found"/>, a bubble); a passed round searches the points within <paramref name="SearchedRadius"/>
    /// of each hiding place (<paramref name="Longer"/>), and the last of <paramref name="Rounds"/> wins
    /// (<paramref name="Won"/>), completing the step. Every message is a ChatConfig row sent as 1022.
    /// </summary>
    public sealed record Seek(int Rounds, float SearchedRadius, IReadOnlyList<int> Opening, int Round, int Countdown, int Longer,
        int Won, int Found);

    /// <summary>
    /// A scene. <paramref name="Unplayed"/> are DungeonQuestConfig quests of the scene that no step shows: the branch
    /// that shows them cannot be reached in the reconstruction (each has its reason in the json, "unplayed").
    /// </summary>
    public sealed record ScriptedScene(long Scene, string Name, string Confidence, IReadOnlyList<ScriptedStep> Steps,
        IReadOnlyList<int> Unplayed, long ContinuesTo = 0, bool LeavesAfterClear = false)
    {
        /// <summary>Milliseconds from the clear to the entry into the scene that continues it (0: DungeonService.ContinueSeconds).</summary>
        public int ContinueAfterMs { get; init; }
    }

    private readonly Dictionary<long, ScriptedScene> _scripted = [];

    public IReadOnlyCollection<ScriptedScene> AllScripted => _scripted.Values;

    /// <summary>The scripted reconstruction of a scene, when the switch is on.</summary>
    public ScriptedScene? Scripted(long scene) => Enabled && _scripted.TryGetValue(scene, out var s) ? s : null;

    private void LoadScriptedScenes()
    {
        using var stream = typeof(ReconstructedDungeons).Assembly.GetManifestResourceStream("Ro3Emu.Server.Reconstructed.scripted-scenes.json")
            ?? throw new InvalidOperationException("Embedded Reconstructed/scripted-scenes.json is missing.");
        using var doc = JsonDocument.Parse(stream);
        foreach (var entry in doc.RootElement.GetProperty("scenes").EnumerateObject())
        {
            long scene = long.Parse(entry.Name, CultureInfo.InvariantCulture);
            var steps = entry.Value.GetProperty("steps").EnumerateArray().Select(Step).ToArray();
            if (steps.Length == 0) throw new InvalidOperationException($"Scripted scene {scene} has no steps.");
            var unplayed = entry.Value.TryGetProperty("unplayed", out var u)
                ? u.EnumerateArray().Select(q => q.GetProperty("quest").GetInt32()).ToArray() : [];
            _scripted[scene] = new ScriptedScene(scene, entry.Value.GetProperty("name").GetString() ?? "",
                entry.Value.GetProperty("confidence").GetString() ?? "", steps, unplayed,
                entry.Value.TryGetProperty("continuesTo", out var next) ? next.GetInt64() : 0,
                entry.Value.TryGetProperty("leavesAfterClear", out var leaves) && leaves.GetBoolean())
            {
                ContinueAfterMs = entry.Value.TryGetProperty("continueAfterMs", out var after) ? after.GetInt32() : 0,
            };
        }
    }

    private static ScriptedStep Step(JsonElement s)
    {
        var goal = s.TryGetProperty("goal", out var g) ? (g[0].GetInt32(), g[1].GetInt32()) : (0, 1);
        var listed = s.TryGetProperty("actors", out var a) ? a.EnumerateArray().ToArray() : [];
        bool IsCompanion(JsonElement x) => x.TryGetProperty("companion", out var c) && c.GetBoolean();
        var actors = listed.Where(x => !IsCompanion(x)).Select(Actor).ToArray();
        var complete = Triggers(s, "complete");
        var fight = s.TryGetProperty("fight", out var f)
            ? f.EnumerateArray().Select(x => new Spawn(x.TryGetProperty("point", out var p) ? p.GetInt64() : 0, x.GetProperty("npc").GetInt32(),
                x.TryGetProperty("route", out var r) ? r.GetInt64() : 0)).ToArray()
            : [];
        if (complete.Count == 0 && fight.Length == 0)
            throw new InvalidOperationException($"Scripted step '{s.GetProperty("stage").GetString()}' cannot complete.");
        return new ScriptedStep(s.GetProperty("stage").GetString() ?? "", s.GetProperty("quest").GetInt32(), goal, actors,
            s.TryGetProperty("story", out var story) ? story.GetInt64() : 0, complete, Triggers(s, "ignore"),
            s.TryGetProperty("after", out var after) && after.TryGetProperty("teleport", out var to) ? to.GetInt64() : 0,
            fight, s.TryGetProperty("all", out var all) && all.GetBoolean(),
            s.TryGetProperty("cannon", out var cannon)
                ? new Cannon(cannon.GetProperty("interaction").GetInt64(), cannon.GetProperty("fire").GetInt32())
                : null,
            s.TryGetProperty("seek", out var seek)
                ? new Seek(seek.GetProperty("rounds").GetInt32(), seek.GetProperty("searchedRadius").GetSingle(),
                    [.. seek.GetProperty("opening").EnumerateArray().Select(v => v.GetInt32())], seek.GetProperty("round").GetInt32(),
                    seek.GetProperty("countdown").GetInt32(), seek.GetProperty("longer").GetInt32(), seek.GetProperty("won").GetInt32(),
                    seek.GetProperty("found").GetInt32())
                : null,
            s.TryGetProperty("shelter", out var shelter)
                ? new Shelter(shelter.GetProperty("boss").GetInt32(), shelter.GetProperty("cover").GetInt32(), shelter.GetProperty("tip").GetInt32(),
                    shelter.GetProperty("skill").GetInt64(), shelter.GetProperty("everyMs").GetInt32(), shelter.GetProperty("warnMs").GetInt32(),
                    shelter.GetProperty("width").GetSingle(), shelter.GetProperty("depth").GetSingle())
                : null,
            s.TryGetProperty("react", out var react) ? ParseReaction(react) : null)
        {
            Companions = [.. listed.Where(IsCompanion).Select(Actor)],
            RideAfter = s.TryGetProperty("after", out var rideAfter) && rideAfter.TryGetProperty("ride", out var ride) ? ride.GetInt64() : 0,
            DriveAfter = s.TryGetProperty("after", out var driveAfter) && driveAfter.TryGetProperty("drive", out var drive) ? drive.GetInt64() : 0,
            RideAlone = s.TryGetProperty("after", out var aloneAfter) && aloneAfter.TryGetProperty("alone", out var alone) && alone.GetBoolean(),
            RideSpeedPercent = s.TryGetProperty("after", out var speedAfter) && speedAfter.TryGetProperty("speed", out var rideSpeed) ? rideSpeed.GetInt32() : 100,
            HoldAfterMs = s.TryGetProperty("after", out var holdAfter) && holdAfter.TryGetProperty("hold", out var hold) ? hold.GetInt32() : 0,
            Guides = s.TryGetProperty("guides", out var guides)
                ? [.. guides.GetProperty("routes").EnumerateArray().Select(v => v.GetInt64())]
                : null,
            Slide = s.TryGetProperty("slide", out var slide)
                ? new Slide(slide.GetProperty("side").GetInt32(), slide.GetProperty("down").GetInt32(),
                    slide.TryGetProperty("endSkill", out var endSkill) ? endSkill.GetInt32() : 0,
                    slide.TryGetProperty("camera", out var camera) ? camera.GetInt32() : 0,
                    slide.TryGetProperty("buff", out var buff) ? buff.GetInt32() : 0)
                : null,
            Booms = s.TryGetProperty("booms", out var booms) ? ParseBooms(booms) : null,
            HoldUntilMs = s.TryGetProperty("after", out var untilAfter) && untilAfter.TryGetProperty("holdUntil", out var until) ? until.GetInt32() : 0,
            Rollers = s.TryGetProperty("rollers", out var rollers)
                ? new Rollers(rollers.GetProperty("boss").GetInt32(),
                    [.. rollers.GetProperty("npcs").EnumerateArray().Select(v => v.GetInt32())],
                    [.. rollers.GetProperty("routes").EnumerateArray().Select(v => v.GetInt64())],
                    rollers.GetProperty("skill").GetInt64(), rollers.GetProperty("firstMs").GetInt32(),
                    rollers.GetProperty("everyMs").GetInt32(), rollers.GetProperty("radius").GetSingle(),
                    rollers.TryGetProperty("tip", out var tip) ? tip.GetInt32() : 0,
                    rollers.TryGetProperty("hitEveryMs", out var hitEvery) ? hitEvery.GetInt32() : 2000)
                {
                    BelowPct = rollers.TryGetProperty("belowPct", out var below) ? below.GetInt32() : 0,
                    Waves = rollers.TryGetProperty("waves", out var waves) ? waves.GetInt32() : 1,
                    WaveMs = rollers.TryGetProperty("waveMs", out var waveMs) ? waveMs.GetInt32() : 0,
                }
                : null,
        };
    }

    private static SceneActor Actor(JsonElement x) => new(x.GetProperty("npc").GetInt32(), x.GetProperty("point").GetInt64(),
        x.TryGetProperty("buffs", out var b) ? b.EnumerateArray().Select(v => v.GetInt32()).ToArray() : [],
        x.TryGetProperty("offset", out var o) ? o[0].GetSingle() : 0, x.TryGetProperty("offset", out o) ? o[1].GetSingle() : 0);

    private static Reaction ParseReaction(JsonElement r) => new(r.GetProperty("speaker").GetInt32(),
        r.TryGetProperty("start", out var start) ? [.. start.EnumerateArray().Select(v => v.GetInt32())] : [],
        r.TryGetProperty("ignored", out var ignored) ? ParseResponse(ignored) : null,
        r.TryGetProperty("complete", out var complete) ? ParseResponse(complete) : null)
    {
        Timed = r.TryGetProperty("timed", out var timed)
            ? [.. timed.EnumerateArray().Select(l => new TimedLine(l.GetProperty("at").GetInt32(), l.GetProperty("say").GetInt32(),
                l.TryGetProperty("speaker", out var who) ? who.GetInt32() : 0))]
            : null,
    };

    private static Response ParseResponse(JsonElement r) => new(
        r.TryGetProperty("say", out var say) ? say.GetInt32() : 0,
        r.TryGetProperty("buff", out var buff) ? buff.GetInt32() : 0,
        r.TryGetProperty("on", out var on) ? on.GetInt32() : 0)
    {
        DelayMs = r.TryGetProperty("delayMs", out var delay) ? delay.GetInt32() : 0,
        Booms = r.TryGetProperty("booms", out var booms) ? ParseBooms(booms) : null,
        Story = r.TryGetProperty("story", out var story) ? story.GetInt64() : 0,
        StoryHoldMs = r.TryGetProperty("storyHoldMs", out var storyHold) ? storyHold.GetInt32() : 0,
        StoryThen = ParsePlots(r),
    };

    private static List<long> ParsePlots(JsonElement r)
    {
        List<long> plots = [];
        if (r.TryGetProperty("storyThen", out var then))
            foreach (var plot in then.EnumerateArray()) plots.Add(plot.GetInt64());
        return plots;
    }

    private static Booms ParseBooms(JsonElement b) => new(b.GetProperty("npc").GetInt32(), b.GetProperty("skill").GetInt64(),
        b.TryGetProperty("shooter", out var shooter) ? shooter.GetInt32() : 0,
        b.TryGetProperty("shooterSkill", out var shooterSkill) ? shooterSkill.GetInt64() : 0,
        b.TryGetProperty("persist", out var persist) && persist.GetBoolean(),
        [.. b.GetProperty("shots").EnumerateArray().Select(v => new Boom(v.GetProperty("at").GetInt32(), v.GetProperty("point").GetInt64()))]);

    private static IReadOnlyList<StepTrigger> Triggers(JsonElement step, string field)
    {
        if (!step.TryGetProperty(field, out var list)) return [];
        var triggers = new List<StepTrigger>();
        foreach (var t in list.EnumerateArray())
        {
            var kinds = Enum.GetValues<TriggerKind>().Where(k => t.TryGetProperty(k.ToString().ToLowerInvariant(), out _)).ToArray();
            if (kinds.Length != 1) throw new InvalidOperationException($"A scripted trigger names {kinds.Length} kinds: {t}");
            var kind = kinds[0];
            triggers.Add(new StepTrigger(kind, t.GetProperty(kind.ToString().ToLowerInvariant()).GetInt64(),
                t.TryGetProperty("radius", out var r) ? r.GetSingle() : 0));
        }
        return triggers;
    }
}
