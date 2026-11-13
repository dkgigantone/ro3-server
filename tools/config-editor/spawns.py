"""Monster spawn editing for the RO3 Plus Config Editor (Mobs page, "Spawn maps").

Your edits live in overrides/spawns.json. "Apply" rewrites the scene files in data/sceneeditor that you touched
(untouched original + your edits, with a backup first). The server reads them when a map line first loads, so restart it.
Nothing here reaches the client.

How the server spawns monsters (CONFIRMED in WorldData.LoadSpawnGroups and MonsterService.EnsureSpawnedAsync)
  * Scene file   data/sceneeditor/<scene>_sceneeditor.json: m_kWorldMonsterSpawnGroups (groups) and
                 m_kWorldMonsterSpawnPoints (points, each belongs to a group by monsterGroupId).
  * Pool group   m_bUseMaxMinRefreshId true. Every entry of m_kMonsterRefreshLimitDatas {m_kConfigId, min, max} spawns a random
                 number between min and max when the map line loads, each on a random free point of the group. A group with
                 fewer free points than it asks for simply spawns fewer. A dead monster comes back after reSpawnTime.x seconds.
  * Fixed group  no such flag. Each point has its own monster (m_kConfigIds[0]); genNumberRandomRange.x of the group's points
                 are filled at once (picked at random, at least 1).
  * The page edits pool counts (min/max per monster, add a monster to a map's pool group, remove it) and the alive total of a
    fixed group. Fixed points are not added or removed. genNumberRandomRange.x and refreshThreshold of a pool group are not
    read by the server, so they are left as they are.
  * Ro3Emu:SpawnLimits (server settings) can still lower a pool count on a map.
Only the maps listed in MapConfig count (public map lines). Standard library only.
"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from common import APP_DIR, LOCK, STATE_DIR, atomic_write, read_json, server_running, sha256

OVERRIDES = APP_DIR / "overrides" / "spawns.json"
MAX_BACKUPS = 5
COUNT_MAX = 5000
SUFFIX = "_sceneeditor.json"


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _index(data):
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    return {str(r["_iID"]): r for r in data if isinstance(r, dict) and "_iID" in r}


def _dump(obj) -> str:
    """Compact JSON like the scene files themselves (same key order, no spaces, real UTF-8)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class SpawnEditor:
    def __init__(self, home: Path):
        self.home = home
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.originals = self.state_dir / "spawns_originals"
        self.backups = self.state_dir / "spawns_backups"
        self.state_file = self.state_dir / "spawns_state.json"
        self._maps = None      # scene file name -> {id, name, type}
        self._npcs = None      # monster id -> name
        self._idx = None       # scene file name -> {"groups": {gid: {...}}}

    # ------------------------------------------------------------ paths and state
    @property
    def scene_dir(self):
        return self.home / "data" / "sceneeditor"

    def live(self, scene):
        return self.scene_dir / scene

    def original(self, scene):
        return self.originals / scene

    def check_home(self):
        for name in ("MapConfig", "NPCConfig", "Localization_en"):
            p = self.home / "data" / "config" / f"{name}.json"
            if not p.is_file():
                raise FileNotFoundError(f"Could not find {p}. Pick the RO3Emu folder that contains data\\config.")
        if not self.scene_dir.is_dir():
            raise FileNotFoundError(f"Could not find {self.scene_dir}.")

    def load_state(self):
        st = read_json(self.state_file, {}) or {}
        st.setdefault("scenes", {})
        return st

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def file_status(self):
        st = self.load_state()
        any_applied = False
        for scene, s in st["scenes"].items():
            p = self.live(scene)
            if not p.is_file():
                return "external"
            cur = sha256(p)
            if s.get("applied_sha"):
                any_applied = True
                if cur != s["applied_sha"]:
                    return "external"
            elif cur != s.get("original_sha"):
                return "external"
        return "applied" if any_applied else "pristine"

    def rebaseline(self):
        with LOCK:
            shutil.rmtree(self.originals, ignore_errors=True)
            self.save_state({"scenes": {}, "applied_overrides": None})
            self._idx = None

    # ------------------------------------------------------------ overrides
    def load_overrides(self):
        d = read_json(OVERRIDES, {}) or {}
        pool = d.get("Pool", {}) if isinstance(d, dict) else {}
        fixed = d.get("Fixed", {}) if isinstance(d, dict) else {}
        return {"Pool": pool if isinstance(pool, dict) else {}, "Fixed": fixed if isinstance(fixed, dict) else {}}

    def save_overrides(self, ov):
        pool = {}
        for scene in sorted(ov["Pool"]):
            groups = {g: ov["Pool"][scene][g] for g in sorted(ov["Pool"][scene], key=int) if ov["Pool"][scene][g]}
            if groups:
                pool[scene] = groups
        fixed = {}
        for scene in sorted(ov["Fixed"]):
            groups = {g: ov["Fixed"][scene][g] for g in sorted(ov["Fixed"][scene], key=int)}
            if groups:
                fixed[scene] = groups
        doc = {"_about": "Edits made with the config editor (Mobs > Spawn maps). Pool: scene file > group id > monster id > [min, max] "
                         "(null removes the monster from that group). Fixed: scene file > group id > how many points are filled at once.",
               "Pool": pool, "Fixed": fixed}
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def count(ov):
        npcs = {n for groups in ov["Pool"].values() for g in groups.values() for n in g}
        return len(npcs) + sum(len(g) for g in ov["Fixed"].values())

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ data
    def _load(self):
        self.check_home()
        if self._maps is None:
            cfg = self.home / "data" / "config"
            loc = read_json(cfg / "Localization_en.json", {}) or {}

            def text(ref):
                rid = ref[0] if isinstance(ref, list) and ref else ref
                e = loc.get(str(rid))
                s = e.get("_kDes", "") if isinstance(e, dict) else ""
                if isinstance(ref, list) and len(ref) > 1:
                    s = s.replace("@{1}", str(ref[1]))
                return s

            maps = {}
            for row in _index(read_json(cfg / "MapConfig.json", {})).values():
                res = row.get("_kResSceneEditorName")
                if isinstance(res, str) and res:
                    maps[res + ".json"] = {"id": int(_num(row.get("_iID"))), "name": text(row.get("_iName")) or res,
                                           "type": int(_num(row.get("_iSceneType")))}
            self._maps = maps
            npcs = {}
            for k, row in _index(read_json(cfg / "NPCConfig.json", {})).items():
                if row.get("_iNPCType") == 1:
                    npcs[int(k)] = text(row.get("_kName"))
            self._npcs = npcs
        if self._idx is None:
            self._idx = self._scan()

    def _read_scene(self, scene):
        p = self.original(scene)
        if not p.is_file():
            p = self.live(scene)
        return read_json(p)

    def _scan(self):
        """Every group of every public map: pool or fixed, its points, its monsters. Read from the untouched originals."""
        out = {}
        for scene in self._maps:
            if not (self.live(scene).is_file() or self.original(scene).is_file()):
                continue
            se = self._read_scene(scene)
            if not isinstance(se, dict):
                continue
            groups_raw = se.get("m_kWorldMonsterSpawnGroups")
            points_raw = se.get("m_kWorldMonsterSpawnPoints")
            if not isinstance(groups_raw, dict) or not groups_raw:
                continue
            points = {}
            for p in (points_raw.values() if isinstance(points_raw, dict) else []):
                if not isinstance(p, dict):
                    continue
                cid = p.get("m_kConfigIds")
                cid = cid[0] if isinstance(cid, list) and cid else 0
                g = points.setdefault(int(_num(p.get("monsterGroupId"), -1)), {"n": 0, "cfg": {}})
                g["n"] += 1
                if cid:
                    g["cfg"][int(cid)] = g["cfg"].get(int(cid), 0) + 1
            groups = {}
            for gid, g in groups_raw.items():
                if not isinstance(g, dict):
                    continue
                pool = g.get("m_bUseMaxMinRefreshId") is True
                limits = {}
                lim = g.get("m_kMonsterRefreshLimitDatas")
                for l in (lim if isinstance(lim, list) else []):
                    if isinstance(l, dict) and l.get("m_kConfigId"):
                        limits[int(l["m_kConfigId"])] = [int(_num(l.get("m_iLimitCount_min"))), int(_num(l.get("m_iLimitCount_max")))]
                pts = points.get(int(_num(g.get("Id"), -1)), {"n": 0, "cfg": {}})
                rng = g.get("genNumberRandomRange")
                groups[str(gid)] = {"pool": pool, "total": int(_num(rng.get("x") if isinstance(rng, dict) else 0)),
                                    "respawn": int(_num((g.get("reSpawnTime") or {}).get("x"), 5)) if isinstance(g.get("reSpawnTime"), dict) else 5,
                                    "points": pts["n"], "cfg": pts["cfg"], "limits": limits}
            if groups:
                out[scene] = groups
        return out

    def invalidate(self):
        self._idx = None

    def _effective_limits(self, scene, gid, ov):
        """The pool group's monsters after the edits: {npc: [min, max]}."""
        lim = dict(self._idx[scene][gid]["limits"])
        for npc, v in (ov["Pool"].get(scene, {}).get(gid, {}) or {}).items():
            if v is None:
                lim.pop(int(npc), None)
            else:
                lim[int(npc)] = list(v)
        return lim

    def _effective_total(self, scene, gid, ov):
        t = ov["Fixed"].get(scene, {}).get(gid)
        return int(t) if t is not None else self._idx[scene][gid]["total"]

    # ------------------------------------------------------------ reading for the page
    def get(self, npc):
        with LOCK:
            self._load()
            npc = int(npc)
            if npc not in self._npcs:
                raise ValueError(f"NPCConfig {npc} is not a monster row.")
            ov = self.load_overrides()
            pools, fixed, addable = [], [], []
            for scene, groups in self._idx.items():
                m = self._maps[scene]
                for gid, g in groups.items():
                    base = {"scene": scene, "mapId": m["id"], "map": m["name"], "group": int(gid), "points": g["points"]}
                    if g["pool"]:
                        eff = self._effective_limits(scene, gid, ov)
                        used = sum(v[1] for v in eff.values())
                        if npc in eff:
                            orig = g["limits"].get(npc)
                            pools.append(base | {"min": eff[npc][0], "max": eff[npc][1], "origMin": orig[0] if orig else None,
                                                 "origMax": orig[1] if orig else None, "usedMax": used, "kinds": len(eff)})
                        elif g["points"] > 0:
                            addable.append(base | {"usedMax": used, "kinds": len(eff)})
                    elif npc in g["cfg"]:
                        fixed.append(base | {"total": self._effective_total(scene, gid, ov), "origTotal": g["total"],
                                             "mine": g["cfg"][npc], "kinds": len(g["cfg"])})
            order = lambda r: (r["mapId"], r["group"])
            return {"npc": npc, "name": self._npcs[npc], "pools": sorted(pools, key=order), "fixed": sorted(fixed, key=order),
                    "addable": sorted(addable, key=order), "edited": self._npc_edited(npc, ov), "meta": self.meta()}

    @staticmethod
    def _npc_edited(npc, ov):
        key = str(npc)
        return any(key in g for groups in ov["Pool"].values() for g in groups.values())

    def meta(self):
        with LOCK:
            ov = self.load_overrides()
            st = self.load_state()
            status = self.file_status()
            nothing = not self.count(ov) and status == "pristine"
            return {"home": str(self.home), "fileStatus": status,
                    "inSync": status != "external" and (nothing or st.get("applied_overrides") == self.ov_hash(ov)),
                    "serverRunning": server_running(), "changed": self.count(ov), "overridesFile": str(OVERRIDES),
                    "countMax": COUNT_MAX}

    # ------------------------------------------------------------ editing
    def set_spawns(self, npc, pools, fixed):
        """pools: [{scene, group, min, max}] = every pool group this monster should be in. fixed: [{scene, group, total}]."""
        with LOCK:
            self._load()
            npc = int(npc)
            if npc not in self._npcs:
                raise ValueError(f"NPCConfig {npc} is not a monster row.")
            ov = self.load_overrides()
            key = str(npc)
            # forget this monster's earlier pool edits, then store the difference to the originals again
            for scene in list(ov["Pool"]):
                for gid in list(ov["Pool"][scene]):
                    ov["Pool"][scene][gid].pop(key, None)
                    if not ov["Pool"][scene][gid]:
                        del ov["Pool"][scene][gid]
                if not ov["Pool"][scene]:
                    del ov["Pool"][scene]
            want = {}
            for e in pools or []:
                scene, gid = str(e.get("scene")), str(int(_num(e.get("group"), -1)))
                g = self._idx.get(scene, {}).get(gid)
                if g is None or not g["pool"]:
                    raise ValueError(f"{scene} group {gid} is not a pool group.")
                if g["points"] <= 0:
                    raise ValueError(f"{self._maps[scene]['name']} group {gid} has no spawn points.")
                lo, hi = e.get("min"), e.get("max")
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) for v in (lo, hi)):
                    raise ValueError("Counts must be whole numbers.")
                lo, hi = int(lo), int(hi)
                if not 0 <= lo <= hi <= COUNT_MAX:
                    raise ValueError(f"Counts must satisfy 0 <= min <= max <= {COUNT_MAX} ({self._maps[scene]['name']}).")
                if (scene, gid) in want:
                    raise ValueError(f"{self._maps[scene]['name']} group {gid} is listed twice.")
                want[(scene, gid)] = [lo, hi]
            for scene, groups in self._idx.items():
                for gid, g in groups.items():
                    if not g["pool"]:
                        continue
                    orig = g["limits"].get(npc)
                    new = want.get((scene, gid))
                    if new is None and orig is not None:
                        ov["Pool"].setdefault(scene, {}).setdefault(gid, {})[key] = None
                    elif new is not None and new != orig:
                        ov["Pool"].setdefault(scene, {}).setdefault(gid, {})[key] = new
            for e in fixed or []:
                scene, gid = str(e.get("scene")), str(int(_num(e.get("group"), -1)))
                g = self._idx.get(scene, {}).get(gid)
                if g is None or g["pool"] or npc not in g["cfg"]:
                    raise ValueError(f"{scene} group {gid} is not a fixed group of this monster.")
                total = e.get("total")
                if isinstance(total, bool) or not isinstance(total, (int, float)) or total != int(total) or not 1 <= int(total) <= g["points"]:
                    raise ValueError(f"The alive total of {self._maps[scene]['name']} group {gid} must be 1 to {g['points']}.")
                total = int(total)
                if total == g["total"]:
                    ov["Fixed"].get(scene, {}).pop(gid, None)
                else:
                    ov["Fixed"].setdefault(scene, {})[gid] = total
            self.save_overrides(ov)
            return self.get(npc)

    def reset_npc(self, npc):
        with LOCK:
            self._load()
            return self.set_spawns(npc, self._original_pools(int(npc)), self._original_fixed(int(npc)))

    def _original_pools(self, npc):
        return [{"scene": s, "group": int(gid), "min": g["limits"][npc][0], "max": g["limits"][npc][1]}
                for s, groups in self._idx.items() for gid, g in groups.items() if g["pool"] and npc in g["limits"]]

    def _original_fixed(self, npc):
        return [{"scene": s, "group": int(gid), "total": g["total"]}
                for s, groups in self._idx.items() for gid, g in groups.items() if not g["pool"] and npc in g["cfg"]]

    def reset_all(self):
        with LOCK:
            self.save_overrides({"Pool": {}, "Fixed": {}})
            return self.meta()

    # ------------------------------------------------------------ apply
    def _backup(self, scene, stamp):
        self.backups.mkdir(parents=True, exist_ok=True)
        stem = scene[:-5]
        shutil.copy2(self.live(scene), self.backups / f"{stem}-{stamp}.json")
        for old in sorted(self.backups.glob(f"{stem}-*.json"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    def build(self, scene, ov):
        """One scene file = the untouched original + the edits."""
        se = read_json(self.original(scene))
        groups = se["m_kWorldMonsterSpawnGroups"]
        for gid, edits in (ov["Pool"].get(scene) or {}).items():
            g = groups.get(gid)
            if not isinstance(g, dict) or g.get("m_bUseMaxMinRefreshId") is not True:
                raise RuntimeError(f"{scene} group {gid} is no longer a pool group; use 'Use current files as new original'.")
            lim = g.get("m_kMonsterRefreshLimitDatas")
            lim = list(lim) if isinstance(lim, list) else []
            for npc, v in edits.items():
                idx = next((i for i, l in enumerate(lim) if isinstance(l, dict) and str(l.get("m_kConfigId")) == npc), None)
                if v is None:
                    if idx is not None:
                        lim.pop(idx)
                elif idx is not None:
                    lim[idx] = dict(lim[idx], m_iLimitCount_min=v[0], m_iLimitCount_max=v[1])
                else:
                    lim.append({"m_iLimitCount_max": v[1], "m_iLimitCount_min": v[0], "m_kConfigId": int(npc)})
            g["m_kMonsterRefreshLimitDatas"] = lim
        for gid, total in (ov["Fixed"].get(scene) or {}).items():
            g = groups.get(gid)
            if not isinstance(g, dict) or not isinstance(g.get("genNumberRandomRange"), dict):
                raise RuntimeError(f"{scene} group {gid} has no genNumberRandomRange.")
            g["genNumberRandomRange"]["x"] = total
        return se

    def apply(self):
        with LOCK:
            self._load()
            st = self.load_state()
            if self.file_status() == "external":
                raise RuntimeError("A spawn scene file was changed outside this tool. Use 'Use current files as new original' first.")
            ov = self.load_overrides()
            touched = sorted(set(ov["Pool"]) | set(ov["Fixed"]))
            for scene in touched:
                if scene not in self._maps or not self.live(scene).is_file():
                    raise RuntimeError(f"Scene file {scene} is missing.")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.originals.mkdir(parents=True, exist_ok=True)
            for scene in touched:                       # keep an untouched copy before the first change to a file
                s = st["scenes"].get(scene)
                if not s or not self.original(scene).is_file():
                    shutil.copy2(self.live(scene), self.original(scene))
                    st["scenes"][scene] = {"original_sha": sha256(self.original(scene)), "applied_sha": None}
            built = {scene: _dump(self.build(scene, ov)) + "\n" for scene in touched}   # every file builds before any is written
            for scene in sorted(st["scenes"]):
                if scene in built:
                    self._backup(scene, stamp)
                    atomic_write(self.live(scene), built[scene])
                    st["scenes"][scene]["applied_sha"] = sha256(self.live(scene))
                elif st["scenes"][scene].get("applied_sha"):   # no edits left on this map: put the original back
                    self._backup(scene, stamp)
                    shutil.copy2(self.original(scene), self.live(scene))
                    st["scenes"][scene]["applied_sha"] = None
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            self._idx = None
            return {"scenes": len(touched), "backup": stamp, "meta": self.meta()}

    def restore_original(self):
        with LOCK:
            self._load()
            st = self.load_state()
            if self.file_status() == "external":
                raise RuntimeError("A spawn scene file was changed outside this tool. Use 'Use current files as new original' first.")
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for scene, s in st["scenes"].items():
                if s.get("applied_sha") and self.original(scene).is_file():
                    self._backup(scene, stamp)
                    shutil.copy2(self.original(scene), self.live(scene))
                    s["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            self._idx = None
            return self.meta()
