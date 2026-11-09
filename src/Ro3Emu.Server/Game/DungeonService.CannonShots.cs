using Romsg;
using Ro3Emu.Server.Maps;

namespace Ro3Emu.Server.Game;

/// <summary>
/// The airship cannon's shots drawn (inferred; visual only, the damage is the ordinary combat's). The client plays the fire
/// skill 5353001 with an empty show (Config/Skill/skill_61098: no m_kShows), so nothing flies towards the ship by itself; the
/// original's server is absent. The siege cannons' own shots are server-side bullet units (TerritoryWarsService.CannonShots:
/// an NPCConfig _iNPCType 10 row flown by MovementMove), so the same kind of unit, the rocket bullet 4900104 (4900101 is the small iron ball), flies here from
/// the gunner to the point the client aimed at. The other airship cannon skill, 5353501, has a show of its own (5353503);
/// !shotfx can add it as a blast at the landing point. Test aids: !shoot, !shotfx.
/// </summary>
public sealed partial class DungeonService
{
    private const long ShotUidFlag = 1L << 40;
    private const double ShotMetresPerSecond = 90;
    private const int ShotMinMs = 250, ShotMaxMs = 1000;
    private long _shotSerial;
    /// <summary>(inferred) The bullet NPC flown for a shot (0: none) and the skill played from the landing point (0: none).</summary>
    private int _shotNpc = 4900104;
    private long[] _shotBlasts = [5353501, 5353101];

    /// <summary>A gunner's shot (CombatService.CannonShot): a bullet from the gunner towards the aimed point.</summary>
    private int OnCannonShot(GameSession s, Vec3 target)
    {
        if (Active(s) is not { } a || a.Settled || _shotNpc == 0) return 0;
        var from = new Vec3 { X = s.Position.X, Y = s.Position.Y + 1.5f, Z = s.Position.Z };
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(1), () => ShotAsync(a, s, from, target), "dungeon cannon shot");
        // The caller delays the shot's damage by this long (0: no fireball flies, the damage is instant).
        double dx = target.X - from.X, dy = target.Y - from.Y, dz = target.Z - from.Z, metres = Math.Sqrt(dx * dx + dy * dy + dz * dz);
        return metres < 1 ? 0 : (int)Math.Clamp(metres / ShotMetresPerSecond * 1000, ShotMinMs, ShotMaxMs);
    }

    private async Task ShotAsync(Attempt a, GameSession s, Vec3 from, Vec3 to)
    {
        if (a.Settled || a.Room.Closed || s.Role == null) return;
        double dx = to.X - from.X, dy = to.Y - from.Y, dz = to.Z - from.Z;
        double metres = Math.Sqrt(dx * dx + dy * dy + dz * dz);
        if (metres < 1) return;
        int flight = (int)Math.Clamp(metres / ShotMetresPerSecond * 1000, ShotMinMs, ShotMaxMs);
        var shot = new AirshipShot
        {
            Uid = SceneActorUidBase | ShotUidFlag | Interlocked.Increment(ref _shotSerial), SceneEntry = a.Plan.SceneEntry,
            InstanceId = a.Room.Id, X = from.X, Y = from.Y, Z = from.Z, Face = MonsterService.Angle(from.X, from.Z, to.X, to.Z),
            NpcId = _shotNpc, Shooter = s.Role.Id, LaunchMs = MonsterService.NowMs, FlightMs = flight,
            Velocity = new Vec3 { X = (float)(dx / flight * 1000), Y = (float)(dy / flight * 1000), Z = (float)(dz / flight * 1000) },
        };
        if (!await _instances.AddPropAsync(a.Room, shot)) return;
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(flight), () => ShotLandedAsync(a, shot, to), "dungeon cannon shot lands");
    }

    private async Task ShotLandedAsync(Attempt a, AirshipShot shot, Vec3 at)
    {
        await _instances.RemovePropAsync(a.Room, shot);
        if (_shotBlasts.Length == 0 || a.Settled || a.Room.Closed) return;
        await ExplodeAsync(a, 5300037, _shotBlasts[0], new MapPoint(at.X, at.Y, at.Z, 0), at, _shotBlasts);
        await BurnAsync(a, at);
    }

    /// <summary>
    /// (inferred) The medium fire the clips leave on the deck after a hit: NPCConfig 5300057 is UnitConfig 5300018 "airship
    /// cannon-fire burning effect" (fx_airship_boomloop_01), 5300058 the impact effect (fx_airship_boatimpact_01). Each is
    /// spawned like the invisible caster, kept a while and removed again; a few at most at a time.
    /// </summary>
    private int _burnNpc = 5300057, _impactNpc = 5300058, _burnMs = 7000, _impactMs = 1500, _burning;

    /// <summary>The enemy ship monsters a hit's fire clings to (5300015 and 5300053, UnitConfig 5300003).</summary>
    private static readonly int[] ShipNpcs = [5300015, 5300053];

    private async Task BurnAsync(Attempt a, Vec3 at)
    {
        // A hit on an enemy ship burns on that ship until it is gone; a blast on our own deck burns on the deck; anything else
        // (the ship died on this very hit) leaves nothing floating in the air.
        var ship = a.Room.Monsters.Where(m => !m.Dead && m.Hp > 0 && ShipNpcs.Contains(m.Template.NpcId))
            .OrderBy(m => (m.Pos.X - at.X) * (m.Pos.X - at.X) + (m.Pos.Z - at.Z) * (m.Pos.Z - at.Z)).FirstOrDefault();
        bool onShip = ship != null && (ship.Pos.X - at.X) * (ship.Pos.X - at.X) + (ship.Pos.Z - at.Z) * (ship.Pos.Z - at.Z) <= 100f;
        bool onDeck = at.X >= 215 && at.X <= 250 && at.Z >= 425 && at.Z <= 495;
        if (!onShip && !onDeck) return;
        Func<bool>? gone = null;
        bool lasting = false;
        int burnMs = onShip ? Math.Min(_burnMs, 5000) : _burnMs;
        if (ship != null && onShip)
        {
            var clung = ship;
            gone = () => clung.Dead || clung.Hp <= 0 || !a.Room.Monsters.Contains(clung);
        }
        else
        {
            // (inferred from the clips) Fire left on our own deck stays: the fires of the early hits until the Evil Boss is
            // defeated (step 7 begins), the ones of the final hits until the scene ends.
            lasting = true;
            burnMs = LastingMs;
            if (a.Wave + 1 <= 6) gone = () => a.Wave + 1 >= 7;
            _log.LogInformation("Scripted scene {Scene}: lasting deck fire at {X:0.0},{Z:0.0} (step {Step})", a.Plan.SceneEntry, at.X, at.Z, a.Wave + 1);
        }
        if (_burnNpc != 0) await EffectAsync(a, _burnNpc, at, burnMs, gone, lasting);
        if (_impactNpc != 0) await EffectAsync(a, _impactNpc, at, _impactMs, onShip ? gone : null);
    }

    private const int LastingMs = 3_600_000;

    /// <summary>(inferred) The huge fire at the Evil Boss's last fifth: several burning-ground effects stacked on one spot of
    /// the deck, plus the screen shake, kept until the boss is defeated.</summary>
    private async Task HugeFireAsync(Attempt a, MonsterUnit boss, int copies)
    {
        float x = Math.Clamp(boss.Pos.X, 215f, 250f), z = Math.Clamp(boss.Pos.Z, 425f, 495f);
        var at = new Vec3 { X = x, Y = boss.Pos.Y, Z = z };
        _log.LogInformation("Scripted scene {Scene}: huge fire ({Copies} stacked) at {X:0.0},{Z:0.0}", a.Plan.SceneEntry, copies, x, z);
        await ExplodeAsync(a, 5300037, 5353201, new MapPoint(x, boss.Pos.Y, z, 0), at, [5353201]);
        Func<bool> gone = () => boss.Dead || boss.Hp <= 0 || a.Wave + 1 >= 7;
        for (int i = 0; i < copies; i++)
            await EffectAsync(a, _burnNpc, at, LastingMs, gone, lasting: true);
    }

    private async Task EffectAsync(Attempt a, int npc, Vec3 at, int lifeMs, Func<bool>? gone, bool lasting = false)
    {
        if (a.Settled || a.Room.Closed || Volatile.Read(ref _burning) >= (lasting ? 40 : 12)) return;
        var fx = await _instances.SpawnAsync(a.Room, npc, new MapPoint(at.X, at.Y, at.Z, 0), grounded: false);
        if (fx == null) return;
        Interlocked.Increment(ref _burning);
        fx.StaysOutOfBattle = true;
        fx.KeepsDamage = true;
        await _world.ShowFromAfarAsync(fx);
        long end = MonsterService.NowMs + lifeMs;
        int removed = 0;
        async Task WatchAsync()
        {
            if (!(a.Settled || a.Room.Closed || MonsterService.NowMs >= end || gone?.Invoke() == true))
            {
                _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(150), WatchAsync, "dungeon burn watch");
                return;
            }
            if (Interlocked.Exchange(ref removed, 1) != 0) return;
            Interlocked.Decrement(ref _burning);
            await _instances.RemoveMonsterAsync(a.Room, fx);
        }
        _loop.ScheduleLegacy(TimeSpan.FromMilliseconds(150), WatchAsync, "dungeon burn watch");
    }

    /// <summary>!burn: the burning-ground NPC (0: none), how long it stays and the impact NPC (0: none).</summary>
    private async Task BurnGmAsync(GameSession s, string[] args)
    {
        if (args.Length == 0) throw new ArgumentException("<npc|0> [lifeMs] [impactNpc|0]");
        _burnNpc = int.Parse(args[0]);
        if (args.Length > 1) _burnMs = int.Parse(args[1]);
        if (args.Length > 2) _impactNpc = int.Parse(args[2]);
        await GmCommands.ReplyAsync(s, $"Cannon hits leave NPC {_burnNpc} for {_burnMs} ms and impact NPC {_impactNpc} (test aid, until restart).");
    }

    /// <summary>A cannonball in flight (NPCConfig _iNPCType 10): its entity carries the whole move, like a siege cannon's bullet.</summary>
    internal sealed class AirshipShot : WorldUnit
    {
        public required int NpcId { get; init; }
        public required long Shooter { get; init; }
        public required Vec3 Velocity { get; init; }
        public required long LaunchMs { get; init; }
        public required int FlightMs { get; init; }

        public override EntityBase Entity() => new()
        {
            Uuid = (ulong)Uid, EntityTemplateID = (ulong)NpcId, EntityType = 10, Owner = Shooter, EntityCamp = 1,
            Face = Face, State = EntityState.Alive, NotifyType = EntityBase.Types.NotifyType.All,
            MovementInfo = new MovementInfo
            {
                Pos = new Vec3 { X = X, Y = Y, Z = Z }, Face = Face, ServerTime = LaunchMs, MoveDuration = FlightMs,
                MoveType = MovementType.MoveTypeMove,
                MovementMove = new MovementMove { StartVelocity = Velocity.Clone(), AccVelocity = new Vec3() },
            },
        };
    }

    /// <summary>!shoot: one test shot from the caller along their facing, as a gunner's would fly.</summary>
    private async Task ShootGmAsync(GameSession s, string[] args)
    {
        if (Active(s) is not { } a) throw new ArgumentException("only inside a dungeon scene");
        double angle = s.Face * Math.PI / 180, metres = args.Length > 0 ? double.Parse(args[0], System.Globalization.CultureInfo.InvariantCulture) : 40;
        var from = new Vec3 { X = s.Position.X, Y = s.Position.Y + 1.5f, Z = s.Position.Z };
        var to = new Vec3 { X = from.X + (float)(Math.Sin(angle) * metres), Y = from.Y, Z = from.Z + (float)(Math.Cos(angle) * metres) };
        await ShotAsync(a, s, from, to);
        await GmCommands.ReplyAsync(s, $"Shot with bullet NPC {_shotNpc}, blast skills {string.Join(',', _shotBlasts)} (test aid).");
    }

    /// <summary>!shotfx: which bullet NPC (0: none) and which landing blast skill (0: none) the cannon's shots use.</summary>
    private async Task ShotFxGmAsync(GameSession s, string[] args)
    {
        if (args.Length == 0) throw new ArgumentException("<bulletNpc|0> [blastSkill|0]");
        _shotNpc = int.Parse(args[0]);
        _shotBlasts = [];
        if (args.Length > 1) _shotBlasts = [.. args[1].Split(',', StringSplitOptions.RemoveEmptyEntries).Select(long.Parse).Where(id => id != 0)];
        await GmCommands.ReplyAsync(s, $"Cannon shots now use bullet NPC {_shotNpc} and blast skills {string.Join(',', _shotBlasts)} (test aid, until the server restarts).");
    }
}
