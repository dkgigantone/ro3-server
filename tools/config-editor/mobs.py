"""Monster editing for the RO3 Plus Config Editor (Mobs page).

Your edits live in overrides/mobs.json (only what you changed, per NPCConfig id). "Apply" rebuilds NPCConfig.json,
AttriMonsterConfig.json and AIConfig.json = untouched originals + your overrides, with a backup first. The server reads
data/config once at startup, so restart it. Nothing here reaches the client: monster stats are the server's.

How a monster's numbers are made (CONFIRMED in MonsterService.TemplateAttributes / Build)
  * Stats        AttriMonsterConfig row (NPCConfig._iAttribute, else _iAttributeVariated * 1000 + _iLevel), each value
                 scaled by the NPC's _kAttriScale per 10000. Attribute ids: 101 HP, 102 SP, 103 Atk, 104 Matk, 105 Pdef,
                 106 Mdef, 109 Hit, 110 Flee, 111 Crit, 113 walk speed. Hit defaults to level+1 and Flee to level.
  * Walk speed   attribute 113 when the row has it, else NPCConfig._iSpeed.
  * Element/Race/Size   NPCConfig._iElement / _iRace / _iSize.
  * Chase range  AIConfig._iUnFightParam[0] (the leash, cm) when the AI has it, else _iChaseDistance.
  * Attack range, swing time and skill range come from the monster's skills (SkillConfig) and are shown read-only.
  * Drops        NPCConfig._iDrop and _iNormalDrop -> MultiverseGroupConfig -> NPCDropConfig / NpcNormalDropConfig ->
                 DropPoolConfig [group, chance per million, rolls, picks] -> DropGroupConfig rows (weights). Editable (see below).

One AttriMonsterConfig row is shared by up to 240 monsters, so an edit never changes the shared row. The edited monster
gets its own row (id 800000000 + npc id) holding the final numbers, and its _kAttriScale is emptied so nothing is
scaled twice. A chase range edit does the same with AIConfig (id 810000000 + npc id). Edits are rebuilt from the
untouched originals each time, so "discard" and "restore original" are exact.
  * Base EXP     item 100 rows of the drop chain above (NPCDropConfig environment 0 and NpcNormalDropConfig; pure item-100
                 groups, 100 % pool chance). Job EXP is never a per-monster value: the server credits
                 PublicParamConfig.BaseToJobConversionPercent per 10000 of the Base EXP (20 %), so it follows the Base EXP.
  * Base EXP edit  The typed number is the monster's Base EXP per kill before the killer's level-gap correction, team and
                 field-rate bonuses (the server applies those afterwards). The monster gets its own copy of the chain
                 (MultiverseGroupConfig 900000000+, drop row 1000000000+, pool 1100000000+, group 1300000000+, all from
                 npc id), every exp row scaled so the first one equals the number. A monster with no Base EXP gets a new
                 NpcNormalDropConfig entry with one exp row. Bonus Time (environment 1) rows are left as they are.
  * Item drops   Edited as a whole list per monster (overrides key "drops"). Each entry: item, chance per roll (percent),
                 rolls, quantity min/max, player level from/to, DropAttenuationConfig plan, plus the quest and map limits
                 it already had. On Apply the monster's own copy of its drop pools gets its item slots emptied (Base EXP
                 slots stay) and the list is written as new NpcNormalDropConfig rows (128 entries per row, one pool slot and one
                 single-row group per entry, so every entry rolls on its own). Ids come from running counters (MV 1500000000+,
                 row 1600000000+, pool 1700000000+, group 1800000000+) in npc id order. Shared rows and Bonus Time (environment 1)
                 are not touched. The level penalty is DropAttenuationConfig (RewardService / DropAttenuation.cs): the picked row's
                 quantity is scaled by the ratio for (player level - monster level).
Standard library only.
"""
import copy
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from common import APP_DIR, LOCK, STATE_DIR, atomic_write, compact, read_json, server_running, sha256

OVERRIDES = APP_DIR / "overrides" / "mobs.json"
TABLES = ("NPCConfig", "AttriMonsterConfig", "AIConfig", "MultiverseGroupConfig", "NPCDropConfig", "NpcNormalDropConfig",
          "DropPoolConfig", "DropGroupConfig")
READ_TABLES = ("ItemConfig", "Localization_en", "MVPConfig", "SkillConfig", "PublicParamConfig", "DropAttenuationConfig")
BASE_EXP_ITEM = 100
NEW_MV_BASE, NEW_ROW_BASE, NEW_POOL_BASE, NEW_GROUP_BASE = 900_000_000, 1_000_000_000, 1_100_000_000, 1_300_000_000
# item drop edits use running counters (a monster can need hundreds of groups), handed out in npc id order on every build
DROP_MV_BASE, DROP_ROW_BASE, DROP_POOL_BASE, DROP_GROUP_BASE = 1_500_000_000, 1_600_000_000, 1_700_000_000, 1_800_000_000
DROP_ROW_CAPACITY = 128          # one NpcNormalDropConfig row: 8 pools x 16 slots
DROP_MAX_ENTRIES = 1000
EXP_TEMPLATE_GROUP = "3305"      # a pure Base EXP group; its row is the template of a new exp row
MAX_BACKUPS = 5
NEW_ATTR_BASE = 800_000_000
NEW_AI_BASE = 810_000_000
AGGRESSIVE_AI = 20000004          # MonsterService.AggressiveAi: aggression is keyed to this AI id, so it must keep it
BOSS_SUBTYPES = (3, 4, 6)         # MonsterService: IsBoss = NPCSubType 3, 4 or 6

# override key -> (label, min, max)
STATS = {"hp": (101, "HP", 1, 2_000_000_000), "sp": (102, "SP", 0, 2_000_000_000), "atk": (103, "Atk", 0, 2_000_000_000),
         "matk": (104, "Matk", 0, 2_000_000_000), "pdef": (105, "Pdef", 0, 2_000_000_000),
         "mdef": (106, "Mdef", 0, 2_000_000_000), "hit": (109, "Hit", 0, 1_000_000), "flee": (110, "Flee", 0, 1_000_000),
         "crit": (111, "Crit", 0, 1_000_000)}
OTHER = {"element": ("Element", 0, 10), "race": ("Race", 0, 10), "size": ("Size", 0, 3),
         "speed": ("Walk speed", 0, 1_000_000), "chase": ("Chase range (m)", 1, 100_000), "exp": ("Base EXP", 0, 2_000_000_000)}
FIELD_LIMITS = {k: (v[2], v[3]) for k, v in STATS.items()} | {k: (v[1], v[2]) for k, v in OTHER.items()}

# From the client's own monsters (matched against well-known names), element order differs from RO1's list.
ELEMENTS = {0: "(none)", 1: "Neutral", 2: "Wind", 3: "Earth", 4: "Fire", 5: "Water", 6: "Poison", 7: "Holy",
            8: "Shadow", 9: "Ghost", 10: "Undead"}
RACES = {0: "(none)", 1: "Brute", 2: "Demi-Human", 3: "Demon", 4: "Plant", 5: "Undead", 6: "Formless", 7: "Fish (likely)",
         8: "Angel", 9: "Insect", 10: "Dragon (likely)"}
SIZES = {0: "(none)", 1: "Small", 2: "Medium", 3: "Large"}
CLASSES = ("all", "normal", "boss", "mvp")


def _index(data):
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    return {str(r["_iID"]): r for r in data if isinstance(r, dict) and "_iID" in r}


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _first_num(v):
    if isinstance(v, list):
        return v[0] if v else 0
    return v


def _pairs(value):
    """[[a, b], ...] from a list of pairs (an empty {} means none)."""
    out = []
    if isinstance(value, list):
        for p in value:
            if isinstance(p, list) and len(p) >= 2 and isinstance(p[0], (int, float)) and isinstance(p[1], (int, float)):
                out.append((int(p[0]), p[1]))
    return out


def _first_id(ref):
    return ref[0] if isinstance(ref, list) and ref else ref if isinstance(ref, int) else 0


class MobsEditor:
    def __init__(self, home: Path):
        self.home = home
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.originals = self.state_dir / "mobs_originals"
        self.backups = self.state_dir / "mobs_backups"
        self.state_file = self.state_dir / "mobs_state.json"
        self._item_names = None  # (id, name) list for the Add drop search, built on first use
        self.ref = None          # read-only lookup tables, loaded once
        self.orig = None         # original rows of the three edited tables

    # ------------------------------------------------------------ paths and state
    @property
    def config_dir(self):
        return self.home / "data" / "config"

    def live(self, table):
        return self.config_dir / f"{table}.json"

    def original(self, table):
        return self.originals / f"{table}.json"

    def check_home(self):
        for t in TABLES + READ_TABLES:
            if not self.live(t).is_file():
                raise FileNotFoundError(f"Could not find {self.live(t)}. Pick the RO3Emu folder that contains data\\config.")

    def load_state(self):
        return read_json(self.state_file, {}) or {}

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def ensure_original(self):
        st = self.load_state()
        shas = st.get("original_sha")
        if not shas or not any(self.original(t).is_file() for t in TABLES):
            self.originals.mkdir(parents=True, exist_ok=True)
            for t in TABLES:
                shutil.copy2(self.live(t), self.original(t))
            st = {"original_sha": {t: sha256(self.original(t)) for t in TABLES}, "applied_sha": None}
            self.save_state(st)
        elif set(shas) != set(TABLES):
            # an older version of this page tracked three tables: the new ones were never edited, so the live files are their originals
            self.originals.mkdir(parents=True, exist_ok=True)
            for t in TABLES:
                if t not in shas:
                    shutil.copy2(self.live(t), self.original(t))
                    shas[t] = sha256(self.original(t))
            applied = st.get("applied_sha")
            if isinstance(applied, dict):
                for t in TABLES:
                    applied.setdefault(t, shas[t])
            self.save_state(st)
        return st

    def file_status(self):
        st = self.ensure_original()
        cur = {t: sha256(self.live(t)) for t in TABLES}
        if cur == st.get("original_sha"):
            return "pristine"
        if cur == st.get("applied_sha"):
            return "applied"
        return "external"

    def rebaseline(self):
        with LOCK:
            self.originals.mkdir(parents=True, exist_ok=True)
            for t in TABLES:
                shutil.copy2(self.live(t), self.original(t))
            self.save_state({"original_sha": {t: sha256(self.original(t)) for t in TABLES}, "applied_sha": None})
            self.orig = None

    # ------------------------------------------------------------ overrides
    def load_overrides(self):
        d = read_json(OVERRIDES, {}) or {}
        mobs = d.get("Mobs", {}) if isinstance(d, dict) else {}
        return {"Mobs": mobs if isinstance(mobs, dict) else {}}

    def save_overrides(self, ov):
        doc = {"_about": "Edits made with the config editor (Mobs). Only changed fields are stored, per NPCConfig id. "
                         "Units: chase in metres, speed as NPCConfig._iSpeed, the rest as the game tables.",
               "Mobs": {k: ov["Mobs"][k] for k in sorted(ov["Mobs"], key=int) if ov["Mobs"][k]}}
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def count(ov):
        return len(ov["Mobs"])

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ data
    def _load(self):
        self.check_home()
        self.ensure_original()
        if self.orig is None:
            self.orig = {t: read_json(self.original(t)) for t in TABLES}
            for t in TABLES:
                if t != "DropGroupConfig":      # DropGroupConfig is group id -> list of rows
                    self.orig[t] = _index(self.orig[t])
        if self.ref is None:
            r = {t: read_json(self.live(t), {}) or {} for t in READ_TABLES if t != "SkillConfig"}
            r["Item"] = _index(r.pop("ItemConfig"))
            r["Loc"] = r.pop("Localization_en")
            mvp = r.pop("MVPConfig")
            r["Mvp"] = {int(x.get("_iNPCID", 0)) for x in (mvp.values() if isinstance(mvp, dict) else mvp) if isinstance(x, dict)}
            plans = {}
            raw = r.pop("DropAttenuationConfig") or []
            for x in raw.values() if isinstance(raw, dict) else raw:
                if isinstance(x, dict):
                    a, b = _num(_first_num(x.get("_kLvDiffLowerlimit"))), _num(_first_num(x.get("_kLvDiffUpLimit")))
                    plans.setdefault(int(_num(x.get("_iPlanID"))), []).append([int(min(a, b)), int(max(a, b)), int(_num(x.get("_iAttenuationRatio")))])
            r["Atten"] = {k: sorted(v) for k, v in plans.items()}
            params = r.pop("PublicParamConfig")
            r["JobPct"] = int(_num(params.get("BaseToJobConversionPercent"), 0)) if isinstance(params, dict) else 0
            self.ref = r
            self._skills = _index(read_json(self.live("SkillConfig"), {}))
            # the drop tables come from the untouched originals, so the page never reads its own edits back
            self.ref.update({"MV": self.orig["MultiverseGroupConfig"], "Pool": self.orig["DropPoolConfig"], "Group": self.orig["DropGroupConfig"],
                             "NDrop": self.orig["NPCDropConfig"], "NNormal": self.orig["NpcNormalDropConfig"]})

    def text(self, ref):
        e = self.ref["Loc"].get(str(_first_id(ref)))
        return e.get("_kDes", "") if isinstance(e, dict) else ""

    def item_name(self, iid):
        row = self.ref["Item"].get(str(iid))
        return self.text(row.get("_iName")) if row else ""

    def mob_ids(self):
        return [k for k, v in self.orig["NPCConfig"].items() if v.get("_iNPCType") == 1]

    def _attr_row(self, npc):
        level = int(_num(npc.get("_iLevel")))
        aid = int(_num(npc.get("_iAttribute")))
        if not aid:
            aid = int(_num(npc.get("_iAttributeVariated"))) * 1000 + level
        return self.orig["AttriMonsterConfig"].get(str(aid))

    def base_attrs(self, npc):
        """MonsterService.TemplateAttributes: the row's values scaled by _kAttriScale per 10000."""
        attr = {}
        row = self._attr_row(npc)
        if row:
            for i, v in _pairs(row.get("_kAttri")):
                attr[i] = float(v)
        for i, scale in _pairs(npc.get("_kAttriScale")):
            if i in attr:
                attr[i] = attr[i] * scale / 10000
        return attr

    def _ai_row(self, npc):
        return self.orig["AIConfig"].get(str(int(_num(npc.get("_iAI")))))

    def _normal_attack(self, npc):
        """The monster's normal attack row (its _kSkills entry flagged _iIsNormalAtk), and its other skills."""
        normal, others = None, []
        skills = npc.get("_kSkills")
        for sid in (skills if isinstance(skills, list) else []):
            row = self._skills.get(str(int(_num(sid))))
            if not row:
                continue
            if normal is None and _num(row.get("_iIsNormalAtk")) == 1:
                normal = row
            else:
                others.append(row)
        return normal, others

    def _exp_group(self, gid):
        """The rows of a drop group when every row is Base EXP (all 315 exp groups of the client are), else None."""
        rows = self.ref["Group"].get(str(gid))
        if isinstance(rows, list) and rows and all(isinstance(x, dict) and x.get("_kDropItem") == BASE_EXP_ITEM for x in rows):
            return rows
        return None

    def exp_sources(self, npc):
        """Where a kill's Base EXP comes from: the NPCDropConfig environment 0 row of _iDrop and each _iNormalDrop row.
        Each source: via, idx (index in _iNormalDrop), mv id, row id, and its pools with the slots that hold an exp group."""
        r = self.ref
        found = []

        def scan(via, idx, mv_id, row_id, env_row):
            pools = []
            for field, pid in env_row.items():
                if not field.startswith("_iDropPool") or not isinstance(pid, int) or not pid:
                    continue
                pool = r["Pool"].get(str(pid))
                slots = []
                for slot in range(1, 17):
                    g = pool.get(f"_iDropGroup_{slot}") if pool else None
                    if isinstance(g, list) and len(g) >= 2 and self._exp_group(g[0]) is not None:
                        rows = self._exp_group(g[0])
                        slots.append({"slot": slot, "group": int(g[0]), "value": round((_num(rows[0].get("_iMinNumber")) + _num(rows[0].get("_iMaxNumber"))) / 2)})
                if slots:
                    pools.append({"field": field, "pool": pid, "slots": slots})
            if pools:
                found.append({"via": via, "idx": idx, "mv": mv_id, "row": row_id, "pools": pools,
                              "value": sum(sl["value"] for p in pools for sl in p["slots"])})

        mv = r["MV"].get(str(int(_num(npc.get("_iDrop")))))
        if mv:
            rid = int(_num(mv.get("_iValue_default")))
            nd = r["NDrop"].get(str(rid))
            if isinstance(nd, dict) and isinstance(nd.get("0"), dict):
                scan("drop", None, int(_num(npc.get("_iDrop"))), rid, nd["0"])
        normals = npc.get("_iNormalDrop")
        for i, n in enumerate(normals if isinstance(normals, list) else []):
            mv = r["MV"].get(str(int(_num(n))))
            rid = int(_num(mv.get("_iValue_default"))) if mv else 0
            row = r["NNormal"].get(str(rid)) if rid else None
            if row:
                scan("normal", i, int(_num(n)), rid, row)
        return found

    def effective(self, nid, ov=None):
        """Everything the page shows for one monster, with the saved edits applied."""
        self._load()
        ov = ov or self.load_overrides()
        npc = self.orig["NPCConfig"][str(nid)]
        e = ov["Mobs"].get(str(nid), {})
        level = int(_num(npc.get("_iLevel")))
        attr = self.base_attrs(npc)

        def stat(key):
            if key in e:
                return e[key]
            i = STATS[key][0]
            if i in attr:
                return round(attr[i])
            return level + 1 if key == "hit" else level if key == "flee" else 0

        ai = self._ai_row(npc) or {}
        # MonsterService.Build: the leash _iUnFightParam[0] when the AI has one, else _iChaseDistance, else 18 m
        param = ai.get("_iUnFightParam")
        if isinstance(param, list) and param and isinstance(param[0], (int, float)):
            chase_cm = param[0]
        else:
            chase_cm = _num(ai.get("_iChaseDistance")) if _num(ai.get("_iChaseDistance")) > 0 else 1800
        speed = e["speed"] if "speed" in e else round(attr[113]) if 113 in attr else _num(npc.get("_iSpeed"))
        normal, others = self._normal_attack(npc)
        skill_range = max((_num(r.get("_iDistanceMax")) for r in others), default=0)
        mvp = int(nid) in self.ref["Mvp"]
        sub = int(_num(npc.get("_iNPCSubType")))
        out = {"id": int(nid), "name": self.text(npc.get("_kName")), "level": level, "subType": sub, "mvp": mvp,
               "class": "mvp" if mvp else "boss" if sub in BOSS_SUBTYPES else "normal",
               "element": e.get("element", int(_num(npc.get("_iElement")))), "race": e.get("race", int(_num(npc.get("_iRace")))),
               "size": e.get("size", int(_num(npc.get("_iSize")))), "speed": speed,
               "chase": e["chase"] if "chase" in e else round(chase_cm / 100, 2),
               "view": round(_num(ai.get("_iViewRange")) / 100, 2), "ai": int(_num(npc.get("_iAI"))),
               "attackRange": round(max(1, _num(normal.get("_iDistanceMax")) / 100), 2) if normal else 2.0,
               "swingMs": int(max(max(300, _num(normal.get("_iActionTime"))), _num(normal.get("_iCD")))) if normal else 2000,
               "ownAttack": normal is not None,
               "skillRange": round(skill_range / 100, 2) if others else None, "skills": len(others),
               "sharedAttrRow": int(_num(npc.get("_iAttribute"))), "edited": sorted(e)}
        for key in STATS:
            out[key] = stat(key)
        out["chaseLocked"] = out["ai"] == AGGRESSIVE_AI
        sources = self.exp_sources(npc)
        out["exp"] = e["exp"] if "exp" in e else sum(x["value"] for x in sources)
        out["expSources"] = len(sources)
        out["jobExp"] = out["exp"] * self.ref["JobPct"] // 10000
        return out

    # ------------------------------------------------------------ drops (read-only)
    def original_drops(self, nid):
        """The item drops a kill rolls from the untouched chain, as editable entries (per roll chance in percent), and
        the Base EXP rows. Each pool slot is [group, chance per million, rolls, picks]; a row's chance per roll is the
        pool chance times its weight share (approximate when a slot picks several rows)."""
        self._load()
        npc = self.orig["NPCConfig"][str(nid)]
        r = self.ref
        rows = []   # (row, maps)
        mid = r["MV"].get(str(int(_num(npc.get("_iDrop")))))
        did = int(_num(mid.get("_iValue_default"))) if mid else 0
        nd = r["NDrop"].get(str(did)) if did else None
        if isinstance(nd, dict) and isinstance(nd.get("0"), dict):
            rows.append((nd["0"], []))
        normals = npc.get("_iNormalDrop")
        for n in (normals if isinstance(normals, list) else []):
            mv = r["MV"].get(str(int(_num(n))))
            row = r["NNormal"].get(str(int(_num(mv.get("_iValue_default"))))) if mv else None
            if row:
                maps = row.get("_iMapId")
                rows.append((row, [int(m) for m in maps] if isinstance(maps, list) else []))
        items, exp = [], []
        for row, maps in rows:
            for k, pid in row.items():
                if not k.startswith("_iDropPool") or not isinstance(pid, int) or not pid:
                    continue
                pool = r["Pool"].get(str(pid))
                if not pool:
                    continue
                for slot in range(1, 17):
                    g = pool.get(f"_iDropGroup_{slot}")
                    if not isinstance(g, list) or len(g) < 2:
                        continue
                    chance = _num(g[1]) / 1_000_000
                    rolls = max(1, min(1000, int(_num(g[2], 1)))) if len(g) >= 3 else 1
                    picks = max(1, int(_num(g[3], 1))) if len(g) >= 4 else 1
                    gid = int(_num(g[0]))
                    grows = r["Group"].get(str(gid)) or []
                    cands = [(i, x) for i, x in enumerate(grows) if isinstance(x, dict) and _num(x.get("_iWeights")) > 0
                             and _num(x.get("_iActivityid")) == 0]
                    total = sum(_num(x["_iWeights"]) for _i, x in cands)
                    for i, x in cands:
                        per_roll = chance * min(1.0, picks * _num(x["_iWeights"]) / total) if total else 0
                        iid = int(_num(x.get("_kDropItem")))
                        if round(per_roll * 100, 4) < 0.0001:
                            continue            # a row that can never drop (chance 0)
                        entry = {"item": iid, "name": self.item_name(iid) or ("Base EXP" if iid == 100 else ""),
                                 "chance": round(per_roll * 100, 4), "rolls": rolls,
                                 "perKill": round((1 - (1 - per_roll) ** rolls) * 100, 4),
                                 "min": int(_num(x.get("_iMinNumber"))), "max": int(_num(x.get("_iMaxNumber"))),
                                 "minLevel": int(_num(x.get("_iMinLevelLimit"))), "maxLevel": int(_num(x.get("_iMaxLevelLimit"))),
                                 "plan": int(_num(x.get("_iAttenuationPlanID"))),
                                 "quest": int(_num(x.get("_iQuestLimit"))), "maps": maps,
                                 "src": {"group": gid, "i": i}}
                        (exp if iid in (100, 101) else items).append(entry)
        items.sort(key=lambda d: (-d["perKill"], d["item"]))
        return items, exp, int(_num(npc.get("_iDrop"))), npc.get("_iNormalDrop") or []

    def drops(self, nid, ov=None):
        items, exp, drop_id, normal = self.original_drops(nid)
        stored = (ov or self.load_overrides())["Mobs"].get(str(nid), {}).get("drops")
        shown = [self._shown(e) for e in stored] if stored is not None else items
        return {"items": shown, "original": items, "exp": exp, "dropId": drop_id, "normalDrop": normal, "edited": stored is not None}

    def _shown(self, e):
        out = dict(e)
        out["name"] = self.item_name(e["item"])
        out["perKill"] = round((1 - (1 - e["chance"] / 100) ** e["rolls"]) * 100, 4)
        return out

    # ------------------------------------------------------------ list
    def search(self, q="", lo=0, hi=0, cls="all", race=-1, size=-1, element=-1, sort="level", desc=False, page=1, limit=100, unnamed=False):
        with LOCK:
            self._load()
            ov = self.load_overrides()
            q = (q or "").strip().lower()
            rows = []
            for nid in self.mob_ids():
                npc = self.orig["NPCConfig"][nid]
                lv = int(_num(npc.get("_iLevel")))
                if lo and lv < lo or hi and lv > hi:
                    continue
                mvp = int(nid) in self.ref["Mvp"]
                sub = int(_num(npc.get("_iNPCSubType")))
                kind = "mvp" if mvp else "boss" if sub in BOSS_SUBTYPES else "normal"
                if cls != "all" and kind != cls:
                    continue
                e = ov["Mobs"].get(nid, {})
                if race >= 0 and e.get("race", int(_num(npc.get("_iRace")))) != race:
                    continue
                if size >= 0 and e.get("size", int(_num(npc.get("_iSize")))) != size:
                    continue
                if element >= 0 and e.get("element", int(_num(npc.get("_iElement")))) != element:
                    continue
                name = self.text(npc.get("_kName"))
                if not name and not unnamed and q != nid:
                    continue            # rows with no English name (test and template rows) stay hidden unless asked for
                if q and q not in name.lower() and q != nid:
                    continue
                rows.append((nid, name, lv, kind))
            keyf = {"name": lambda r: (r[1].lower(), r[2]), "id": lambda r: int(r[0])}.get(sort, lambda r: (r[2], r[1].lower()))
            rows.sort(key=keyf, reverse=bool(desc))
            if sort in ("hp", "exp"):
                rows.sort(key=lambda r: self.effective(r[0], ov)[sort], reverse=bool(desc))
            total = len(rows)
            limit = max(1, min(int(limit), 200))
            page = max(1, int(page))
            part = rows[(page - 1) * limit: page * limit]
            return {"total": total, "page": page, "limit": limit, "mobs": [self.effective(r[0], ov) for r in part]}

    def get(self, nid):
        with LOCK:
            self._load()
            if str(nid) not in self.orig["NPCConfig"] or str(nid) not in set(self.mob_ids()):
                raise ValueError(f"NPCConfig {nid} is not a monster row.")
            ov = self.load_overrides()
            return {"mob": self.effective(nid, ov), "drops": self.drops(nid, ov), "original": self.effective(nid, {"Mobs": {}}),
                    "meta": self.meta()}

    def meta(self):
        with LOCK:
            status = self.file_status()
            ov = self.load_overrides()
            st = self.load_state()
            return {"home": str(self.home), "fileStatus": status,
                    "inSync": status == "applied" and st.get("applied_overrides") == self.ov_hash(ov),
                    "serverRunning": server_running(), "changedRows": self.count(ov), "overridesFile": str(OVERRIDES),
                    "elements": ELEMENTS, "races": RACES, "sizes": SIZES, "limits": FIELD_LIMITS, "jobPct": self.ref["JobPct"] if self.ref else 0,
                    "plans": self.ref["Atten"] if self.ref else {}, "dropLimit": DROP_MAX_ENTRIES}

    # ------------------------------------------------------------ editing
    def set_fields(self, nid, values):
        """values: {field: number or None}. None (or a value equal to the original) removes that edit."""
        with LOCK:
            self._load()
            nid = str(int(nid))
            if nid not in self.mob_ids():
                raise ValueError(f"NPCConfig {nid} is not a monster row.")
            original = self.effective(nid, {"Mobs": {}})
            ov = self.load_overrides()
            edit = dict(ov["Mobs"].get(nid, {}))
            for key, value in values.items():
                if key not in FIELD_LIMITS:
                    raise ValueError(f"'{key}' cannot be edited.")
                if value is None or value == "":
                    edit.pop(key, None)
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number.")
                value = round(value, 2) if key == "chase" else int(value)
                lo, hi = FIELD_LIMITS[key]
                if not lo <= value <= hi:
                    raise ValueError(f"{key} must be between {lo} and {hi}.")
                if key == "chase" and original["chaseLocked"]:
                    raise ValueError("This monster uses AI 20000004 (the aggressive AI); the server keys aggression to that AI id, "
                                     "so its chase range cannot be edited without making it passive.")
                if value == original[key]:
                    edit.pop(key, None)
                else:
                    edit[key] = value
            if edit:
                ov["Mobs"][nid] = edit
            else:
                ov["Mobs"].pop(nid, None)
            self.save_overrides(ov)
            return self.get(nid)

    def item_search(self, q, limit=15):
        """Items by id or part of the English name, for the Add drop box."""
        with LOCK:
            self._load()
            q = (q or "").strip().lower()
            if not q:
                return {"items": []}
            if self._item_names is None:
                self._item_names = [(int(k), self.text(v.get("_iName")) if isinstance(v, dict) else "") for k, v in self.ref["Item"].items()]
                self._item_names.sort()
            out = []
            if q.isdigit() and q in self.ref["Item"]:
                out.append({"id": int(q), "name": self.item_name(q)})
            for iid, name in self._item_names:
                if len(out) >= limit:
                    break
                if name and q in name.lower() and iid not in (100, 101) and not (q.isdigit() and iid == int(q)):
                    out.append({"id": iid, "name": name})
            return {"items": out}

    def set_drops(self, nid, entries):
        """entries: the monster's complete item drop list (None puts the original back). A list equal to the original
        is not stored. Base EXP is edited with the exp field, never here."""
        with LOCK:
            self._load()
            nid = str(int(nid))
            if nid not in self.mob_ids():
                raise ValueError(f"NPCConfig {nid} is not a monster row.")
            ov = self.load_overrides()
            edit = dict(ov["Mobs"].get(nid, {}))
            if entries is None:
                edit.pop("drops", None)
            else:
                if not isinstance(entries, list):
                    raise ValueError("The drop list must be a list.")
                if len(entries) > DROP_MAX_ENTRIES:
                    raise ValueError(f"A monster can have at most {DROP_MAX_ENTRIES} item drops.")
                clean = [self._clean_entry(e, i + 1) for i, e in enumerate(entries)]
                original, *_rest = self.original_drops(nid)
                if [self._comparable(e) for e in clean] == [self._comparable(e) for e in original]:
                    edit.pop("drops", None)
                else:
                    edit["drops"] = clean
            if edit:
                ov["Mobs"][nid] = edit
            else:
                ov["Mobs"].pop(nid, None)
            self.save_overrides(ov)
            return self.get(nid)

    @staticmethod
    def _comparable(e):
        return (e["item"], e["chance"], e["rolls"], e["min"], e["max"], e["minLevel"], e["maxLevel"], e["plan"])

    def _clean_entry(self, e, n):
        if not isinstance(e, dict):
            raise ValueError(f"Drop {n}: bad row.")

        def num(key, lo, hi, integer=True, default=None):
            v = e.get(key, default)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"Drop {n}: {key} must be a number.")
            if not lo <= v <= hi:
                raise ValueError(f"Drop {n}: {key} must be between {lo} and {hi}.")
            return int(v) if integer else round(float(v), 4)

        item = num("item", 1, 2_000_000_000)
        if item in (100, 101):
            raise ValueError(f"Drop {n}: item {item} is EXP; use the Base EXP field.")
        if str(item) not in self.ref["Item"]:
            raise ValueError(f"Drop {n}: item {item} does not exist in ItemConfig.")
        chance = num("chance", 0.0001, 100, integer=False)
        out = {"item": item, "chance": chance, "rolls": num("rolls", 1, 1000, default=1),
               "min": num("min", 0, 1_000_000_000), "max": num("max", 0, 1_000_000_000),
               "minLevel": num("minLevel", 0, 999, default=0), "maxLevel": num("maxLevel", 0, 999, default=999)}
        if out["max"] < out["min"]:
            raise ValueError(f"Drop {n}: max quantity is below min quantity.")
        if out["maxLevel"] and out["maxLevel"] < out["minLevel"]:
            raise ValueError(f"Drop {n}: max player level is below min player level.")
        plan = num("plan", 0, 1_000_000, default=0)
        if plan and str(plan) not in {str(k) for k in self.ref["Atten"]} and plan not in self.ref["Atten"]:
            raise ValueError(f"Drop {n}: penalty plan {plan} does not exist.")
        out["plan"] = plan
        out["quest"] = int(_num(e.get("quest")))
        maps = e.get("maps")
        out["maps"] = [int(m) for m in maps] if isinstance(maps, list) and all(isinstance(m, (int, float)) for m in maps) else []
        src = e.get("src")
        out["src"] = {"group": int(src["group"]), "i": int(src["i"])} if isinstance(src, dict) and "group" in src and "i" in src else None
        return out

    def reset_mob(self, nid):
        with LOCK:
            ov = self.load_overrides()
            ov["Mobs"].pop(str(int(nid)), None)
            self.save_overrides(ov)
            return self.get(nid)

    def reset_all(self):
        with LOCK:
            self.save_overrides({"Mobs": {}})
            return self.meta()

    # ------------------------------------------------------------ apply
    def _backup(self, stamp):
        self.backups.mkdir(parents=True, exist_ok=True)
        for t in TABLES:
            shutil.copy2(self.live(t), self.backups / f"{t}-{stamp}.json")
            for old in sorted(self.backups.glob(f"{t}-*.json"))[:-MAX_BACKUPS]:
                old.unlink(missing_ok=True)

    def build(self, ov):
        """The three tables = originals + the edits. Returns (tables, changed monster count)."""
        self._load()
        tables = {t: read_json(self.original(t)) for t in TABLES}
        npcs, attrs, ais = (_index(tables[t]) for t in TABLES[:3])
        done = 0
        ids = {"mv": DROP_MV_BASE, "row": DROP_ROW_BASE, "pool": DROP_POOL_BASE, "group": DROP_GROUP_BASE}
        for nid, e in sorted(ov["Mobs"].items(), key=lambda kv: int(kv[0])):
            npc = npcs.get(nid)
            if npc is None or not e:
                continue
            level = int(_num(npc.get("_iLevel")))
            if any(k in e for k in STATS) or "speed" in e:
                final = {i: v for i, v in self.base_attrs(npc).items()}
                for key, (aid, *_rest) in STATS.items():
                    if key in e:
                        final[aid] = e[key]
                if "speed" in e and 113 in final:
                    final[113] = e["speed"]
                new_id = NEW_ATTR_BASE + int(nid)
                row = {"_iCorrectionID": 0, "_iID": new_id, "_iLevel": level, "_iMonsterTypeID": 0, "_iOpenDay": 0, "_iScore": 0,
                       "_kAttri": [[i, int(round(v))] for i, v in sorted(final.items())]}
                attrs[str(new_id)] = row
                tables["AttriMonsterConfig"][str(new_id)] = row
                npc["_iAttribute"] = new_id
                npc["_kAttriScale"] = {}
            if "exp" in e:
                self._apply_exp(int(nid), npc, e["exp"], tables)
            if "drops" in e:
                self._apply_drops(int(nid), npc, e["drops"], tables, ids)
            for key, field in (("element", "_iElement"), ("race", "_iRace"), ("size", "_iSize"), ("speed", "_iSpeed")):
                if key in e:
                    npc[field] = e[key]
            if "chase" in e:
                ai = self._ai_row(npc)
                if ai is not None and int(_num(npc.get("_iAI"))) != AGGRESSIVE_AI:
                    new_ai = copy.deepcopy(ai)
                    new_id = NEW_AI_BASE + int(nid)
                    cm = int(round(e["chase"] * 100))
                    new_ai["_iID"] = new_id
                    new_ai["_iChaseDistance"] = cm
                    param = new_ai.get("_iUnFightParam")
                    if isinstance(param, list) and param:
                        new_ai["_iUnFightParam"] = [cm] + param[1:]
                    tables["AIConfig"][str(new_id)] = new_ai
                    npc["_iAI"] = new_id
            done += 1
        return tables, done

    def _apply_exp(self, nid, npc, target, tables):
        """Give the monster its own copy of the drop chain whose Base EXP rows add up to `target`."""
        sources = [x for x in self.exp_sources(npc) if x["value"] > 0] or []
        mvt, ndt, nnt, pt, gt = (tables[t] for t in ("MultiverseGroupConfig", "NPCDropConfig", "NpcNormalDropConfig", "DropPoolConfig", "DropGroupConfig"))
        counter = {"pool": 0, "group": 0}
        made = []       # the new exp rows, so rounding drift can be put right at the end

        def next_id(kind, base):
            if counter[kind] >= 8:
                raise ValueError(f"Monster {nid} has too many Base EXP rows to edit.")
            counter[kind] += 1
            return base + nid * 8 + counter[kind] - 1

        if sources:
            total = sum(x["value"] for x in sources)
            factor = target / total
            for k, src in enumerate(sources):
                new_mv, new_row = NEW_MV_BASE + nid * 4 + k, NEW_ROW_BASE + nid * 4 + k
                mv_row = copy.deepcopy(mvt[str(src["mv"])])
                mv_row["_iID"], mv_row["_iValue_default"] = new_mv, new_row
                mvt[str(new_mv)] = mv_row
                if src["via"] == "drop":
                    row = copy.deepcopy(ndt[str(src["row"])])
                    for env in row.values():
                        if isinstance(env, dict) and "_iID" in env:
                            env["_iID"] = new_row
                    env_row = row["0"]
                    ndt[str(new_row)] = row
                    npc["_iDrop"] = new_mv
                else:
                    row = copy.deepcopy(nnt[str(src["row"])])
                    row["_iID"] = new_row
                    env_row = row
                    nnt[str(new_row)] = row
                    normals = list(npc["_iNormalDrop"])
                    normals[src["idx"]] = new_mv
                    npc["_iNormalDrop"] = normals
                for pref in src["pools"]:
                    new_pool = next_id("pool", NEW_POOL_BASE)
                    pool = copy.deepcopy(pt[str(pref["pool"])])
                    pool["_iID"] = new_pool
                    for sl in pref["slots"]:
                        new_gid = next_id("group", NEW_GROUP_BASE)
                        rows = copy.deepcopy(gt[str(sl["group"])])
                        for x in rows:
                            x["_iID"] = new_gid
                            x["_iMinNumber"] = max(0, int(round(_num(x["_iMinNumber"]) * factor)))
                            x["_iMaxNumber"] = max(x["_iMinNumber"], int(round(_num(x["_iMaxNumber"]) * factor)))
                        made.append(rows[0])
                        gt[str(new_gid)] = rows
                        pool[f"_iDropGroup_{sl['slot']}"][0] = new_gid
                    pt[str(new_pool)] = pool
                    env_row[pref["field"]] = new_pool
            drift = target - sum(round((x["_iMinNumber"] + x["_iMaxNumber"]) / 2) for x in made)
            if drift and made:
                made[0]["_iMinNumber"] = max(0, made[0]["_iMinNumber"] + drift)
                made[0]["_iMaxNumber"] = max(made[0]["_iMinNumber"], made[0]["_iMaxNumber"] + drift)
            return
        if target <= 0:
            return
        # no Base EXP yet: a new NpcNormalDropConfig entry with one exp row, built from rows the client already has
        tpl_group, tpl_pool, tpl_mv = gt.get(EXP_TEMPLATE_GROUP), pt.get("240001"), mvt.get("240001")
        tpl_row = nnt.get("100240001")
        if not (tpl_group and tpl_pool and tpl_mv and tpl_row):
            raise ValueError("The template drop rows (group 3305, pool 240001, NpcNormalDropConfig 100240001) are missing.")
        gid, pid, rid, mid = next_id("group", NEW_GROUP_BASE), next_id("pool", NEW_POOL_BASE), NEW_ROW_BASE + nid * 4, NEW_MV_BASE + nid * 4
        grow = copy.deepcopy(tpl_group[0])
        grow.update({"_iID": gid, "_iMinNumber": target, "_iMaxNumber": target, "_iMinLevelLimit": 0, "_iMaxLevelLimit": 999,
                     "_iQuestLimit": 0, "_iWeights": 100})
        gt[str(gid)] = [grow]
        pool = copy.deepcopy(tpl_pool)
        for key in list(pool):
            if key.startswith("_iDropGroup_"):
                pool[key] = {}
        pool["_iID"], pool["_iDropGroup_1"] = pid, [gid, 1000000, 1, 1]
        pt[str(pid)] = pool
        nrow = copy.deepcopy(tpl_row)
        for key in list(nrow):
            if key.startswith("_iDropPool"):
                nrow[key] = 0
        nrow.update({"_iID": rid, "_iDropPool1": pid, "_iMapId": {}})
        nnt[str(rid)] = nrow
        mv_row = copy.deepcopy(tpl_mv)
        mv_row["_iID"], mv_row["_iValue_default"] = mid, rid
        mvt[str(mid)] = mv_row
        existing = npc.get("_iNormalDrop")
        npc["_iNormalDrop"] = (list(existing) if isinstance(existing, list) else []) + [mid]

    def _apply_drops(self, nid, npc, entries, tables, ids):
        """The monster's item drops become exactly `entries`. The item slots of its own pool copies are emptied (Base EXP
        slots stay), then the list is written as new NpcNormalDropConfig rows: one pool slot and one single-row group per
        entry, so each entry rolls on its own with its chance. Shared rows are never edited."""
        mvt, ndt, nnt, pt, gt = (tables[t] for t in ("MultiverseGroupConfig", "NPCDropConfig", "NpcNormalDropConfig", "DropPoolConfig", "DropGroupConfig"))

        def take(kind):
            ids[kind] += 1
            return ids[kind] - 1

        def item_slots(pool):
            out = []
            for slot in range(1, 17):
                g = pool.get(f"_iDropGroup_{slot}")
                if isinstance(g, list) and len(g) >= 2:
                    rows = gt.get(str(int(_num(g[0])))) or []
                    if not (rows and all(isinstance(x, dict) and x.get("_kDropItem") in (100, 101) for x in rows)):
                        out.append(slot)
            return out

        def clear(env_row):
            """Copy every pool of this drop row that holds item slots and empty those slots in the copy."""
            changed = False
            for field, pid in list(env_row.items()):
                if field.startswith("_iDropPool") and isinstance(pid, int) and pid and str(pid) in pt:
                    slots = item_slots(pt[str(pid)])
                    if slots:
                        pool = copy.deepcopy(pt[str(pid)])
                        new_pool = take("pool")
                        pool["_iID"] = new_pool
                        for slot in slots:
                            pool[f"_iDropGroup_{slot}"] = {}
                        pt[str(new_pool)] = pool
                        env_row[field] = new_pool
                        changed = True
            return changed

        # 1. the monster's own sources: copy MultiverseGroupConfig and the drop row when an item slot has to go
        mv = mvt.get(str(int(_num(npc.get("_iDrop")))))
        rid = int(_num(mv.get("_iValue_default"))) if mv else 0
        if rid and isinstance(ndt.get(str(rid)), dict) and isinstance(ndt[str(rid)].get("0"), dict):
            row = copy.deepcopy(ndt[str(rid)])
            if clear(row["0"]):
                new_mv, new_row = take("mv"), take("row")
                for env in row.values():
                    if isinstance(env, dict) and "_iID" in env:
                        env["_iID"] = new_row
                ndt[str(new_row)] = row
                mv_row = copy.deepcopy(mv)
                mv_row["_iID"], mv_row["_iValue_default"] = new_mv, new_row
                mvt[str(new_mv)] = mv_row
                npc["_iDrop"] = new_mv
        normals = npc.get("_iNormalDrop")
        normals = list(normals) if isinstance(normals, list) else []
        for i, n in enumerate(normals):
            mv = mvt.get(str(int(_num(n))))
            rid = int(_num(mv.get("_iValue_default"))) if mv else 0
            if rid and str(rid) in nnt:
                row = copy.deepcopy(nnt[str(rid)])
                if clear(row):
                    new_mv, new_row = take("mv"), take("row")
                    row["_iID"] = new_row
                    nnt[str(new_row)] = row
                    mv_row = copy.deepcopy(mv)
                    mv_row["_iID"], mv_row["_iValue_default"] = new_mv, new_row
                    mvt[str(new_mv)] = mv_row
                    normals[i] = new_mv
        # 2. the new list, grouped by the maps it is limited to, 128 entries per row
        tpl_group, tpl_pool, tpl_mv = gt.get(EXP_TEMPLATE_GROUP), pt.get("240001"), mvt.get("240001")
        tpl_row = nnt.get("100240001")
        if entries and not (tpl_group and tpl_pool and tpl_mv and tpl_row):
            raise ValueError("The template drop rows (group 3305, pool 240001, NpcNormalDropConfig 100240001) are missing.")
        by_maps = {}
        for e in entries:
            by_maps.setdefault(tuple(e.get("maps") or []), []).append(e)
        for maps, chunk_all in by_maps.items():
            for start in range(0, len(chunk_all), DROP_ROW_CAPACITY):
                chunk = chunk_all[start:start + DROP_ROW_CAPACITY]
                new_mv, new_row = take("mv"), take("row")
                nrow = copy.deepcopy(tpl_row)
                for key in list(nrow):
                    if key.startswith("_iDropPool") or key.startswith("_iDropType"):
                        nrow.pop(key)
                nrow["_iID"] = new_row
                nrow["_iMapId"] = list(maps) if maps else {}
                for pn, pstart in enumerate(range(0, len(chunk), 16), start=1):
                    new_pool = take("pool")
                    pool = copy.deepcopy(tpl_pool)
                    for key in list(pool):
                        if key.startswith("_iDropGroup_"):
                            pool[key] = {}
                    pool["_iID"] = new_pool
                    for slot, e in enumerate(chunk[pstart:pstart + 16], start=1):
                        gid = take("group")
                        src = e.get("src")
                        base = (self.orig["DropGroupConfig"].get(str(src["group"])) or []) if src else []
                        grow = copy.deepcopy(base[src["i"]] if src and 0 <= src["i"] < len(base) else tpl_group[0])
                        grow.update({"_iID": gid, "_ilineID": gid, "_iSerial": 1, "_iWeights": 1, "_kDropItem": e["item"],
                                     "_iMinNumber": e["min"], "_iMaxNumber": e["max"], "_iMinLevelLimit": e["minLevel"],
                                     "_iMaxLevelLimit": e["maxLevel"], "_iAttenuationPlanID": e["plan"], "_iQuestLimit": e.get("quest", 0),
                                     "_iActivityid": 0})
                        gt[str(gid)] = [grow]
                        pool[f"_iDropGroup_{slot}"] = [gid, int(round(e["chance"] * 10000)), e["rolls"], 1]
                    pt[str(new_pool)] = pool
                    nrow[f"_iDropPool{pn}"] = new_pool
                    nrow[f"_iDropType{pn}"] = 1
                nnt[str(new_row)] = nrow
                mv_row = copy.deepcopy(tpl_mv)
                mv_row["_iID"], mv_row["_iValue_default"] = new_mv, new_row
                mvt[str(new_mv)] = mv_row
                normals.append(new_mv)
        before = npc.get("_iNormalDrop")
        if normals != (list(before) if isinstance(before, list) else []):
            npc["_iNormalDrop"] = normals

    def apply(self):
        with LOCK:
            self._load()
            st = self.ensure_original()
            if self.file_status() == "external":
                raise RuntimeError("NPCConfig, AttriMonsterConfig or AIConfig was changed outside this tool. "
                                   "Use 'Use current files as new original' first.")
            ov = self.load_overrides()
            tables, done = self.build(ov)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._backup(stamp)
            for t in TABLES:
                atomic_write(self.live(t), compact(tables[t]) + "\n")
            st["applied_sha"] = {t: sha256(self.live(t)) for t in TABLES}
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            return {"mobs": done, "backup": stamp, "meta": self.meta()}

    def restore_original(self):
        with LOCK:
            self._load()
            st = self.ensure_original()
            self._backup(datetime.now().strftime("%Y%m%d-%H%M%S"))
            for t in TABLES:
                shutil.copy2(self.original(t), self.live(t))
            st["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            return self.meta()
