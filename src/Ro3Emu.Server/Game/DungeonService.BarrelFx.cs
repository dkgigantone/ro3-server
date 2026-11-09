using Ro3Emu.Server.Maps;
using Romsg;

namespace Ro3Emu.Server.Game;

/// <summary>
/// The airship barrel rounds' pose (inferred from the clips: the ship tilts nose-down and the players slip towards the front
/// while barrels roll). Two parts, both off-by-data test aids first:
/// - the push: every PushTickMs each participant is moved PushCmPerSec towards where the barrel lanes run (the first point of
///   a lane to its last), kept inside the lanes' bounds, so the player slips but can still walk against it;
/// - the camera: the client's slide controller switches to a Camera row (the slide sits the character down, so it is off
///   by default: _barrelCamera 0).
/// Test aid: !barrelcam &lt;camera|0&gt; [pushCm|0] [sideCm] [downCm] (negative pushCm: the other way).
/// </summary>
public sealed partial class DungeonService
{
    private int _barrelCamera, _barrelPushCm = 0, _barrelSideCm = 50, _barrelDownCm = 50;
    private const int PushTickMs = 50;
    private const float PushMargin = 6f;

    private async Task BarrelFxStartAsync(Attempt a, long endMs, List<IReadOnlyList<(float X, float Y, float Z)>> lanes)
    {
        if (_barrelCamera != 0)
        {
            await StartSlideAsync(a, new ReconstructedDungeons.Slide(_barrelSideCm, _barrelDownCm, 0, _barrelCamera));
            _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(Math.Max(1, endMs - MonsterService.NowMs) + 500), async () =>
            {
                foreach (var member in Present(a).Where(Sliding).ToList()) await StopSlideAsync(member);
            }, "dungeon barrel slide end");
        }
        if (_barrelPushCm == 0 || lanes.Count == 0) return;
        float dx = 0, dz = 0, minX = float.MaxValue, maxX = float.MinValue, minZ = float.MaxValue, maxZ = float.MinValue;
        foreach (var lane in lanes)
        {
            if (lane.Count < 2) continue;
            dx += lane[^1].X - lane[0].X;
            dz += lane[^1].Z - lane[0].Z;
            foreach (var p in lane)
            {
                minX = Math.Min(minX, p.X); maxX = Math.Max(maxX, p.X);
                minZ = Math.Min(minZ, p.Z); maxZ = Math.Max(maxZ, p.Z);
            }
        }
        float len = MathF.Sqrt(dx * dx + dz * dz);
        if (len < 0.01f) return;
        dx /= len; dz /= len;
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(PushTickMs), () => PushTickAsync(a, endMs, dx, dz, (minX - PushMargin, maxX + PushMargin, minZ - PushMargin, maxZ + PushMargin)), "dungeon barrel slip");
    }

    private async Task PushTickAsync(Attempt a, long endMs, float dx, float dz, (float MinX, float MaxX, float MinZ, float MaxZ) box)
    {
        if (a.Settled || a.Room.Closed || MonsterService.NowMs > endMs) return;
        float step = _barrelPushCm / 100f * PushTickMs / 1000f;
        foreach (var member in Present(a).Where(m => a.Loaded.Contains(m) && !m.SceneLoading && m.Role != null).ToList())
        {
            float x = Math.Clamp(member.Position.X + dx * step, box.MinX, box.MaxX), z = Math.Clamp(member.Position.Z + dz * step, box.MinZ, box.MaxZ);
            await _scenes.TeleportInMapAsync(member, new MapPoint(x, member.Position.Y, z, member.Face));
        }
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(PushTickMs), () => PushTickAsync(a, endMs, dx, dz, box), "dungeon barrel slip");
    }

    private async Task BarrelCamGmAsync(GameSession s, string[] args)
    {
        if (args.Length == 0) throw new ArgumentException("<camera|0> [pushCm|0] [sideCm] [downCm]");
        _barrelCamera = int.Parse(args[0]);
        if (args.Length > 1) _barrelPushCm = int.Parse(args[1]);
        if (args.Length > 2) _barrelSideCm = int.Parse(args[2]);
        if (args.Length > 3) _barrelDownCm = int.Parse(args[3]);
        await GmCommands.ReplyAsync(s, $"Barrel rounds: camera {_barrelCamera} (0: none), push {_barrelPushCm} cm/s towards the lanes' front (negative: back, 0: none), slide {_barrelSideCm}/{_barrelDownCm}.");
    }
}
