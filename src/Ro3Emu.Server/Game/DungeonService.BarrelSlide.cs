using Romsg;

namespace Ro3Emu.Server.Game;

/// <summary>
/// GM test aid !barrelslide (TEST AID, not original): puts the calling player into the client's own slide, with a chosen
/// Camera row, to find out on the airship (1000501) whether a flat deck slides slowly, in which pose, and how a zoomed-out
/// camera looks and is restored. Uses the same custom data as a scripted step's slide (DungeonService.Slide).
/// </summary>
public sealed partial class DungeonService
{
    private async Task BarrelSlideGmAsync(GameSession s, string[] args)
    {
        if (SetCustomData is not { } set) throw new ArgumentException("custom data is not available");
        string sub = args.Length == 0 ? "" : args[0].ToLowerInvariant();
        if (sub == "off")
        {
            await StopSlideAsync(s);
            await GmCommands.ReplyAsync(s, "Slide off (test aid).");
            return;
        }
        if (sub != "on") throw new ArgumentException("on [camera=1005] [sideCmPerSec=50] [downCmPerSec=50] | off");
        int camera = args.Length > 1 ? int.Parse(args[1]) : 1005;
        int side = args.Length > 2 ? int.Parse(args[2]) : 50;
        int down = args.Length > 3 ? int.Parse(args[3]) : 50;
        await set(s, new CustomData
        {
            Id = SlideSpeedData,
            CustomParam = new CustomParam { Tp = CustomParam.Types.ParamType.ListLong, ListLongParam = { side, down } },
        });
        if (camera != 0) await set(s, LongData(SlideCameraData, camera));
        await set(s, SlideState(1));
        await GmCommands.ReplyAsync(s, $"Slide on (test aid): camera {camera}, side {side} cm/s, down {down} cm/s. '!barrelslide off' stops it.");
    }
}
