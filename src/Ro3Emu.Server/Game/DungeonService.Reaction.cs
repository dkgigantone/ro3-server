using Romsg;
using Ro3Emu.Server.Data.Config;

namespace Ro3Emu.Server.Game;

/// <summary>
/// A scripted step's reactions (ReconstructedDungeons.Reaction, inferred as to when they go out): the speaker's ChatConfig
/// lines as 1022 with its uid, as Moonlight Flower's game sends hers, and a BuffConfig effect on the actor standing where
/// the player made a choice. Louie's 3120703 shell game: his lines (Secretary rows 5310002-5310004, 5310008, 5310009)
/// and the right/wrong Bell effects (5402101 SXGD_RIGHT, 5402201 SXGD_WRONG, whose sound and effects play when the buff
/// starts) are in the client, but nothing in the client sends them.
/// </summary>
public sealed partial class DungeonService
{
    /// <summary>The step's opening lines, each once the previous one's display time has run out.</summary>
    private void StartReaction(Attempt a, ReconstructedDungeons.Reaction react)
    {
        int generation = a.StepGeneration, at = 0;
        foreach (int chat in react.Start)
        {
            StepLater(a, generation, at, () => SayAsync(a, react.Speaker, chat));
            at += LineMs(chat);
        }
        foreach (var line in react.Timed ?? [])
            StepLater(a, generation, line.AtMs, () => SayAsync(a, line.Speaker, line.Say), whileFighting: true);
    }

    /// <summary>
    /// Answers an event with the response's line and effect. Returns how long the line stays on screen, which a completing
    /// reaction waits out before the next step.
    /// </summary>
    private async Task<int> RespondAsync(Attempt a, ReconstructedDungeons.Reaction? react,
        ReconstructedDungeons.Response? response, SceneActorUnit? used)
    {
        if (react == null || response == null || a.Settled || a.Room.Closed) return 0;
        if (response.Buff != 0 && used != null
            && a.SceneActors.FirstOrDefault(u => u.NpcId == response.On && u.Point == used.Point) is { } target)
            await ReplayBuffAsync(target, response.Buff);
        if (response.Booms is { } booms) StartBooms(a, a.StepGeneration, booms, persist: true);
        int hold = 0;
        if (response.Say != 0)
        {
            if (response.DelayMs > 0)
            {
                // The step is already done when a completing reaction answers, so the timer must not wait for it; the
                // generation still ends it if the next step starts first.
                StepLater(a, a.StepGeneration, response.DelayMs, () => SayAsync(a, react.Speaker, response.Say), whileFighting: true);
                hold = response.DelayMs + LineMs(response.Say);
            }
            else
            {
                await SayAsync(a, react.Speaker, response.Say);
                hold = LineMs(response.Say);
            }
        }
        if (response.Story != 0)
        {
            // The video follows the line; the step holds for it (inferred length).
            StepLater(a, a.StepGeneration, Math.Max(1, hold), () => PlayStoryAsync(a, response.Story), whileFighting: true);
            hold = Math.Max(1, hold) + Math.Max(0, response.StoryHoldMs);
        }
        return hold;
    }

    /// <summary>Plays a PlotInterpretation story on the clients once (the var-change event of a step's Story).</summary>
    private async Task PlayStoryAsync(Attempt a, long story)
    {
        if (a.Settled || a.Room.Closed) return;
        await BroadcastAsync(a, Event, new DungeonEventNotify
        {
            DungeonEntry = a.Plan.SceneEntry, EventType = (DungeonEventType)4,
            StageNum = a.Wave + 1, LevelNum = 1, Vars = { new DungeonVar { VarId = StoryVar, Vi64 = { story } } },
        });
    }

    /// <summary>Puts the effect on again even when the actor already has it, so its start sound and effects play again.</summary>
    private async Task ReplayBuffAsync(SceneActorUnit target, int buff)
    {
        int[] others = [.. target.Buffs.Select(b => b.ConfigId).Where(id => id != buff)];
        await SetActorBuffsAsync(target, others);
        await SetActorBuffsAsync(target, [.. others, buff]);
    }

    private async Task SayAsync(Attempt a, int speaker, int chat)
    {
        long who = a.SceneActors.FirstOrDefault(u => u.NpcId == speaker)?.Uid ?? 0;
        await BroadcastAsync(a, SystemBroadcast, new SystemBoardCastMsgNtf { Source = MsgSource.Npc, ChatId = chat, PlayerId = who });
        // The portrait rows carry no sound (ChatConfig _iAudio is empty); the older voiced rows say the same lines.
        if (_voiceOn && VoiceTwins.TryGetValue(chat, out int voiced))
            await BroadcastAsync(a, SystemBroadcast, new SystemBoardCastMsgNtf { Source = MsgSource.Npc, ChatId = voiced, PlayerId = who });
    }

    private int LineMs(int chat) => _data.Row("ChatConfig", chat) is { } row ? (int)new ChatConfig(row).Time : 0;

    /// <summary>A scripted scene's delay, shortened by ScriptedScenePacePercent (tests).</summary>
    private TimeSpan Paced(int ms) => TimeSpan.FromMilliseconds((long)ms * Math.Max(1, _options.ScriptedScenePacePercent) / 100);
}
