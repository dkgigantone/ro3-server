"""Item editing for the RO3 Plus Config Editor (Equipment tab first; Cards and Other items follow).

Your edits live in overrides/items.json (only what you changed). "Apply" rebuilds the game tables
(ItemConfig.json, EquipConfig.json) = untouched original + your overrides, with a backup first.

What can be edited today
  * Equipment base stats:  EquipConfig._kBasicAttribute  -> lines of [attribute, min, max]
  * Equipment level need:  ItemConfig._iLevelNeed
  * Enchant slots:         ItemConfig._iEnchantSlot
Edits change items created AFTER the server restarts. Items players already own keep the values rolled
when they were created (the server stores each item's roll on the item itself).
"""
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from common import APP_DIR, LOCK, STATE_DIR, atomic_write, compact, read_json, server_running, sha256

OVERRIDES = APP_DIR / "overrides" / "items.json"
MAX_BACKUPS = 5
EQUIP_TYPE = 5
MAX_LINES = 20                      # the server stores at most 20 base-attribute slots per item
MAX_STAT = 100_000_000

# table -> field -> (low, high) for plain whole-number fields
INT_FIELDS = {
    "ItemConfig": {"_iLevelNeed": (1, 999), "_iEnchantSlot": (0, 8), "_iStackLimit": (0, 999999)},
}
# table -> (field, kind). "range" lines are [stat, min, max]; "pair" lines are [stat, value].
LINE_FIELDS = {"EquipConfig": ("_kBasicAttribute", "range"), "CardConfig": ("_kBasicAttribute", "pair"),
               "CardUpgradeConfig": ("_iAttr", "pair")}
LIST_TABLES = {"CardConfig", "CardUpgradeConfig"}      # stored as a list of rows; the row id is _iID
TABLES = ("ItemConfig", "EquipConfig", "CardConfig", "CardUpgradeConfig")
CARD_TYPE = 4


def rows_of(table):
    """The rows of a config table, whether the file stores them as a list or as an id -> row dict."""
    vals = table.values() if isinstance(table, dict) else table
    return [r for r in vals if isinstance(r, dict)]


def index_of(data):
    """id -> row for a table stored either as an id -> row dict or as a list of rows with _iID."""
    if isinstance(data, dict):
        return data
    return {str(r["_iID"]): r for r in data if isinstance(r, dict) and "_iID" in r}


def describe_effects(use, award, name_of, buff_of, award_rows, action_names):
    """Plain-words lines for an item's _iUseEffect ([action, args...] lists) and _iAward (reward group)."""
    out = []
    for e in use or []:
        if not e or e[0] in ("", None):
            continue
        a, args = str(e[0]), [str(x) for x in e[1:]]
        nm = action_names.get(a, "")
        if a in ("200", "201", "202", "212"):
            what = {"200": "Unlocks an appearance", "201": "Unlocks a mount", "202": "Unlocks an emoji", "212": "Unlocks an emoji set"}[a]
            out.append(f"{what} (id {args[-1] if args else '?'})")
        elif a in ("203", "10"):
            b = buff_of(args[0]) if args else ""
            out.append("Gives buff: " + (b or f"buff {args[0] if args else '?'}") )
        elif a == "100":
            out.append("Restores HP/SP (fixed amount; args " + ", ".join(args) + ")")
        elif a == "207":
            out.append(f"Adds a library page (id {args[0] if args else '?'})")
        elif a == "349":
            out.append(f"Unlocks a social outfit (id {args[0] if args else '?'})")
        elif a in ("206", "608"):
            out.append(f"Teaches a totem skill (id {args[0] if args else '?'})")
        elif a in ("606", "1"):
            out.append("Opens a game window" if a == "1" else f"Uses a skill (id {args[0] if args else '?'})")
        elif a == "607":
            out.append(f"Eat magic food (id {args[0] if args else '?'})")
        elif a == "620":
            out.append(f"Unlocks a personal decoration (id {args[-1] if args else '?'})")
        elif a == "603":
            out.append(f"Adds {args[0] if args else '?'} EXP to the pet incubation room")
        elif a == "105":
            out.append("World horn (shout to everyone)")
        elif a in ("107", "108", "109", "110", "111"):
            what = {"107": "intimacy", "108": "popularity", "109": "affection", "110": "bar hotness", "111": "stars"}[a]
            out.append(f"Gives {args[0] if args else '?'} {what}")
        else:
            out.append(f"{nm or 'Action ' + a}" + (f" ({', '.join(args)})" if args else ""))
    if award:
        rows = award_rows.get(str(award)) or []
        parts = []
        for r in rows:
            for x in (r.get("_kAward") or []):
                if len(x) >= 2:
                    parts.append(f"{x[1]} x {name_of(x[0]) or 'item ' + str(x[0])}")
            if not rows or r.get("_iDropPool"):
                parts.append(f"random drop pool {r.get('_iDropPool')}")
        out.append("Gives: " + ("; ".join(parts) if parts else f"reward group {award}"))
    return out


def norm_line(x, kind="range"):
    """range: [attr, value] and [attr, min, max] both become (attr, min, max). pair: (attr, value)."""
    if kind == "pair":
        return (int(x[0]), int(x[1]))
    if len(x) == 2:
        return (int(x[0]), int(x[1]), int(x[1]))
    return (int(x[0]), int(x[1]), int(x[2]))


def norm_lines(lines, kind="range"):
    return [norm_line(x, kind) for x in lines] if isinstance(lines, list) else []


class ItemsEditor:
    def __init__(self, home: Path):
        self.home = home
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.originals = self.state_dir / "items_originals"
        self.backups = self.state_dir / "items_backups"
        self.state_file = self.state_dir / "items_state.json"
        self.cache = None
        self.base = None

    # ------------------------------------------------------------ paths and state
    @property
    def config_dir(self):
        return self.home / "data" / "config"

    def live(self, table):
        return self.config_dir / f"{table}.json"

    def orig(self, table):
        return self.originals / f"{table}.json"

    def check_home(self):
        for t in TABLES:
            if not self.live(t).is_file():
                raise FileNotFoundError(f"Could not find {self.live(t)}. Pick the RO3Emu folder that contains data\\config.")

    def load_state(self):
        return read_json(self.state_file, {}) or {}

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def ensure_originals(self):
        st = self.load_state()
        changed = False
        for t in TABLES:
            if not self.orig(t).is_file() or not st.get(t, {}).get("original_sha"):
                self.originals.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.live(t), self.orig(t))
                st[t] = {"original_sha": sha256(self.orig(t)), "applied_sha": None}
                changed = True
        if changed:
            self.save_state(st)
        return st

    def table_status(self, t, st):
        cur = sha256(self.live(t))
        s = st.get(t, {})
        if cur == s.get("original_sha"):
            return "pristine"
        if cur == s.get("applied_sha"):
            return "applied"
        return "external"

    def file_status(self):
        st = self.ensure_originals()
        per = {t: self.table_status(t, st) for t in TABLES}
        if "external" in per.values():
            return "external", per
        if "applied" in per.values():
            return "applied", per
        return "pristine", per

    def rebaseline(self):
        with LOCK:
            st = {}
            for t in TABLES:
                shutil.copy2(self.live(t), self.orig(t))
                st[t] = {"original_sha": sha256(self.orig(t)), "applied_sha": None}
            self.save_state(st)
            self.cache = self.base = None

    # ------------------------------------------------------------ overrides
    def load_overrides(self):
        d = read_json(OVERRIDES, {}) or {}
        return {t: d.get(t, {}) for t in TABLES} if isinstance(d, dict) else {t: {} for t in TABLES}

    def save_overrides(self, ov):
        doc = {"_about": "Edits made with plus-config-editor (Items). Only changed fields are stored. "
                         "EquipConfig rows use the equipment's _iEquipId; ItemConfig rows use the item id."}
        for t in TABLES:
            doc[t] = {k: ov[t][k] for k in sorted(ov[t], key=int) if ov[t][k]}
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def count(ov):
        return sum(len(rows) for rows in ov.values())

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ data for the page
    def load_game_data(self):
        items = read_json(self.orig("ItemConfig"))
        equips = read_json(self.orig("EquipConfig"))
        card_rows = read_json(self.orig("CardConfig"))
        upgrade_rows = read_json(self.orig("CardUpgradeConfig"))
        loc = read_json(self.config_dir / "Localization_en.json", {}) or {}
        attr_cfg = read_json(self.config_dir / "AttributeConfig.json", {}) or {}
        types = read_json(self.config_dir / "TypeConfig.json", {}) or {}
        ratio_rows = rows_of(read_json(self.config_dir / "EquipAttriRandom.json", []) or [])
        entry_rand = read_json(self.config_dir / "EquipEntryRandom.json", {}) or {}
        group_rows = rows_of(read_json(self.config_dir / "EquipAttriGroupConfig.json", []) or [])
        sgroup_rows = rows_of(read_json(self.config_dir / "EquipSpecialGroupConfig.json", []) or [])
        action_rows = rows_of(read_json(self.config_dir / "ActionConfig.json", []) or [])
        award_rows = read_json(self.config_dir / "AwardConfig.json", {}) or {}
        buffs = read_json(self.config_dir / "BuffConfig.json", {}) or {}

        def text(ids, plain=False):
            """Localized text of the first id. Names like 'Bow@{1}' carry a variant number in the next list entry
            (shown as ' #1'); in a description the same placeholder is just the number."""
            ids = ids if isinstance(ids, list) else [ids]
            if not ids:
                return ""
            row = loc.get(str(ids[0]))
            if not (isinstance(row, dict) and row.get("_kDes")):
                return ""
            out = str(row["_kDes"])
            for n, arg in enumerate(ids[1:], start=1):
                out = out.replace("@{%d}" % n, (" #%s" % arg) if not plain else str(arg)).replace("${%d}" % n, str(arg))
            return re.sub(r"[@$]\{\d+\}", "", out).strip()

        # equipment slot names (2H, Main-Hand, ...) live in the TypeConfig group whose type 2 is "Main-Hand"
        part_names = {}
        for d in types.values():
            names = {r["_iType"]: text(r.get("_iName", [])) for r in rows_of(d) if "_iType" in r}
            if names.get(2) == "Main-Hand":
                part_names = {str(i): names.get(i, f"Part {i}") for i in range(1, 8)}
                break
        part_names = part_names or {str(i): f"Part {i}" for i in range(1, 8)}
        # card slot names (Weapon, Off-Hand, ... Mouthwear): the TypeConfig group with exactly types 1..9 starting at "Weapon"
        card_parts = {}
        for d in types.values():
            names = {r["_iType"]: text(r.get("_iName", [])) for r in rows_of(d) if "_iType" in r}
            if sorted(names) == list(range(1, 10)) and names.get(1) == "Weapon":
                card_parts = {str(k): v for k, v in names.items()}
                break
        card_parts = card_parts or {str(i): f"Slot {i}" for i in range(1, 10)}

        attrs = {}
        for k, r in attr_cfg.items():
            if isinstance(r, dict):
                attrs[k] = {"name": text(r.get("_iName", [])) or r.get("_kVariable", k), "var": r.get("_kVariable", ""),
                            "pct": r.get("_iDataType", 0) == 1, "type": r.get("_iAttributeType", 0)}

        # how far into [min, max] a roll lands, per item quality (weights from EquipAttriRandom)
        by_q = {}
        for r in ratio_rows:
            if r.get("_iWeight", 0) > 0:
                by_q.setdefault(r["_iQuality"], []).append((r.get("_iRatio", 0), r["_iWeight"]))
        ratio = {}
        for q, rows in by_q.items():
            tot = sum(w for _, w in rows)
            mean = sum(a * w for a, w in rows) / tot
            rows.sort()
            acc, p10, p90 = 0, rows[0][0], rows[-1][0]
            got10 = got90 = False
            for a, w in rows:
                acc += w
                if not got10 and acc >= tot * 0.10:
                    p10, got10 = a, True
                if not got90 and acc >= tot * 0.90:
                    p90, got90 = a, True
            ratio[str(q)] = {"mean": round(mean), "p10": p10, "p90": p90}

        counts = {}
        for r in rows_of(entry_rand):
            if r.get("_iWeight", 0) > 0:
                counts.setdefault(str(r["_iQuality"]), []).append({"n": r["_iNum"], "w": r["_iWeight"]})

        groups = {}
        for r in group_rows:
            if r.get("_iWeight", 0) > 0 and r.get("_iAttriID", 0) > 0:
                groups.setdefault(str(r["_iGroup"]), []).append(
                    {"a": r["_iAttriID"], "min": r["_iMin"], "max": r["_iMax"], "w": r["_iWeight"]})
        sgroups = {}
        for r in sgroup_rows:
            if r.get("_iWeight", 0) > 0:
                sgroups.setdefault(str(r["_iGroup"]), []).append({"e": r["_iSpecialID"], "w": r["_iWeight"]})

        action_names = {str(r["_iInterID"]): r.get("_iFunctionName", "") for r in action_rows}

        def name_of(iid):
            r = items.get(str(iid))
            return text(r.get("_iName", [])) if isinstance(r, dict) else ""

        def buff_of(bid):
            r = buffs.get(str(bid))
            return (text(r.get("_iName", [])) + f" (buff {bid})") if isinstance(r, dict) and text(r.get("_iName", [])) else ""

        base = {t: {} for t in TABLES}
        equipment = []
        usable = []
        by_card = {}
        for r in rows_of(card_rows):
            base["CardConfig"][str(r["_iID"])] = {"_kBasicAttribute": norm_lines(r.get("_kBasicAttribute"), "pair")}
            by_card.setdefault(r["_iCardID"], []).append(r)
        upgrades, upgrade_users = {}, {}
        for r in rows_of(upgrade_rows):
            base["CardUpgradeConfig"][str(r["_iID"])] = {"_iAttr": norm_lines(r.get("_iAttr"), "pair")}
            upgrades.setdefault(f'{r["_iParts"]}:{r["_iQuality"]}', []).append(
                {"row": str(r["_iID"]), "level": r["_iLevel"], "lines": [list(x) for x in norm_lines(r.get("_iAttr"), "pair")],
                 "cost": r.get("_iCost") or []})
        for v in upgrades.values():
            v.sort(key=lambda x: x["level"])
        cards = []
        for iid, row in items.items():
            if not isinstance(row, dict):
                continue
            base["ItemConfig"][iid] = {f: row.get(f, 0) for f in INT_FIELDS["ItemConfig"]}
            if row.get("_iType") == CARD_TYPE:
                tiers = sorted(by_card.get(int(iid), []), key=lambda r: r.get("_iTier", 0))
                if not tiers:
                    continue
                part, qual = row.get("_iCardPart", 0), row.get("_iQuality", 0)
                upgrade_users[f"{part}:{qual}"] = upgrade_users.get(f"{part}:{qual}", 0) + 1
                cards.append({"id": iid, "name": text(row.get("_iName", [])) or f"(unnamed card {iid})", "quality": qual, "part": part,
                              "tiers": [{"row": str(t["_iID"]), "tier": t.get("_iTier", 0), "cap": t.get("_iLevel", 0),
                                         "lines": [list(x) for x in norm_lines(t.get("_kBasicAttribute"), "pair")],
                                         "cost": t.get("_kCost") or [], "skills": t.get("_kSkillAttribute") or [],
                                         "flash": bool(t.get("_kFlashAttribute"))} for t in tiers]})
                continue
            if row.get("_iType") != EQUIP_TYPE:
                nm = text(row.get("_iName", []))
                if nm:
                    desc = text(row.get("_iDescription", []), plain=True)
                    usable.append({"id": iid, "name": nm, "type": row.get("_iType", 0), "sub": row.get("_iSubType", 0),
                                   "quality": row.get("_iQuality", 0), "level": row.get("_iLevelNeed", 1),
                                   "stack": row.get("_iStackLimit", 0), "use": bool(row.get("_iIsUse")),
                                   "desc": desc,
                                   "effects": describe_effects(row.get("_iUseEffect"), row.get("_iAward"), name_of, buff_of,
                                                               award_rows, action_names)})
                continue
            eid = str(row.get("_iEquipId", 0))
            e = equips.get(eid)
            if not isinstance(e, dict):
                continue
            base["EquipConfig"][eid] = {"_kBasicAttribute": norm_lines(e.get("_kBasicAttribute"))}
            equipment.append({
                "id": iid, "equipId": eid, "name": text(row.get("_iName", [])) or f"(unnamed item {iid})",
                "quality": row.get("_iQuality", 0), "level": row.get("_iLevelNeed", 1),
                "part": row.get("_iEquipPart", 0), "sub": row.get("_iSubType", 0), "slots": row.get("_iEnchantSlot", 0),
                "base": [{"a": a, "min": lo, "max": hi} for a, lo, hi in norm_lines(e.get("_kBasicAttribute"))],
                "group": e.get("_iEntries", 0) or 0,
                "fixed": e.get("_kFixedEntries") or [],
                "special": e.get("_kSpecial") or [],
                "fixedSpecial": e.get("_kFixedSpecialAttribute") or [],
                "appraise": e.get("_iIfAppraise", 0),
            })
        equipment.sort(key=lambda x: (x["name"].lower(), x["quality"], int(x["id"])))
        usable.sort(key=lambda x: (x["name"].lower(), int(x["id"])))
        self.base = base
        cards.sort(key=lambda x: (x["name"].lower(), int(x["id"])))
        return {"usable": usable, "cards": cards, "cardParts": card_parts, "upgrades": upgrades, "upgradeUsers": upgrade_users,
                "equipment": equipment, "attrs": attrs,
                "partNames": part_names,
                "ratio": ratio, "entryCounts": counts, "groups": groups, "specialGroups": sgroups}

    def payload(self):
        with LOCK:
            self.check_home()
            self.ensure_originals()
            if self.cache is None:
                self.cache = self.load_game_data()
            data = dict(self.cache)
            data["overrides"] = self.load_overrides()
            data["status"] = self.meta()
            return data

    def meta(self):
        with LOCK:
            status, per = self.file_status()
            ov = self.load_overrides()
            st = self.load_state()
            return {
                "home": str(self.home), "fileStatus": status, "perTable": per,
                "inSync": status == "applied" and st.get("applied_overrides") == self.ov_hash(ov),
                "serverRunning": server_running(), "changedRows": self.count(ov),
                "overridesFile": str(OVERRIDES),
            }

    # ------------------------------------------------------------ editing
    def _edit(self, ov, table, row, field, value):
        base = self.base
        if table not in TABLES or row not in base.get(table, {}):
            raise ValueError(f"{table} row {row} does not exist.")
        entry = ov[table].setdefault(row, {})
        orig = base[table][row]
        if field in INT_FIELDS.get(table, {}):
            if value is None:
                entry.pop(field, None)
            else:
                lo, hi = INT_FIELDS[table][field]
                if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                    raise ValueError(f"{field} must be a whole number between {lo} and {hi}.")
                if value == orig[field]:
                    entry.pop(field, None)
                else:
                    entry[field] = value
        elif table in LINE_FIELDS and field == LINE_FIELDS[table][0]:
            kind = LINE_FIELDS[table][1]
            if value is None:
                entry.pop(field, None)
            else:
                lines = self._check_lines(value, kind)
                if lines == orig[field]:
                    entry.pop(field, None)
                else:
                    entry[field] = [list(x) for x in lines]
        else:
            raise ValueError(f"{field} cannot be edited here.")
        if not entry:
            ov[table].pop(row, None)

    def _check_lines(self, value, kind="range"):
        attrs = (self.cache or {}).get("attrs", {})
        if not isinstance(value, list) or len(value) > MAX_LINES:
            raise ValueError(f"An item can have at most {MAX_LINES} base stat lines.")
        out, seen = [], set()
        for x in value:
            ok_len = (2,) if kind == "pair" else (2, 3)
            if not isinstance(x, (list, tuple)) or len(x) not in ok_len or any(isinstance(v, bool) or not isinstance(v, int) for v in x):
                raise ValueError("Each stat line needs a stat and a value (whole numbers)." if kind == "pair"
                                 else "Each stat line needs a stat, a minimum and a maximum (whole numbers).")
            n = norm_line(x, kind)
            a, lo, hi = (n[0], n[1], n[1]) if kind == "pair" else n
            if str(a) not in attrs:
                raise ValueError(f"Stat {a} does not exist in the game.")
            if a in seen:
                raise ValueError(f"{attrs[str(a)]['name']} is listed twice on the same item.")
            if lo > hi:
                raise ValueError(f"{attrs[str(a)]['name']}: the minimum ({lo}) is higher than the maximum ({hi}).")
            if abs(lo) > MAX_STAT or abs(hi) > MAX_STAT:
                raise ValueError(f"{attrs[str(a)]['name']}: values must stay within ±{MAX_STAT:,}.")
            seen.add(a)
            out.append((a, lo) if kind == "pair" else (a, lo, hi))
        return out

    def set_many(self, items):
        """All-or-nothing: every edit is checked first; the overrides file is written once."""
        with LOCK:
            self.check_home()
            self.ensure_originals()
            if self.cache is None:
                self.cache = self.load_game_data()
            ov = self.load_overrides()
            for it in items:
                self._edit(ov, it["table"], str(it["row"]), it["field"], it.get("value"))
            self.save_overrides(ov)
            return self.meta()

    def reset_all(self):
        with LOCK:
            self.save_overrides({t: {} for t in TABLES})
            return self.meta()

    # ------------------------------------------------------------ apply / restore
    def _backup(self, t, stamp):
        self.backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.live(t), self.backups / f"{t}-{stamp}.json")
        for old in sorted(self.backups.glob(f"{t}-*.json"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    def write_overlay(self, ov):
        """client-overlay/config-overlay.tsv: the edited card rows for the client hook (ConfigOverlay), so the card
        window shows the edited numbers. Copy it to <client>\\ro3emu\\custom\\. Always rewritten (empty = nothing edited)."""
        lines = ["# RO3 Plus Config Editor: edited rows the client should show. Table<TAB>Row<TAB>Field<TAB>attribute:value;..."]
        for table, (field, kind) in LINE_FIELDS.items():
            if kind != "pair":
                continue
            for rid, flds in sorted(ov.get(table, {}).items(), key=lambda kv: int(kv[0])):
                if field in flds:
                    lines.append("\t".join([table, rid, field, ";".join(f"{a}:{v}" for a, v in flds[field])]))
        path = APP_DIR / "client-overlay" / "config-overlay.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        tmp.replace(path)
        return str(path)

    def apply(self):
        with LOCK:
            self.check_home()
            st = self.ensure_originals()
            status, per = self.file_status()
            if status == "external":
                bad = ", ".join(t + ".json" for t, s in per.items() if s == "external")
                raise RuntimeError(f"{bad} was changed outside this tool. Use 'Use current files as new original' first.")
            ov = self.load_overrides()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            written, fields, skipped = [], 0, []
            for t in TABLES:
                rows = ov[t]
                if not rows and per[t] == "pristine":
                    continue                       # nothing to write and the game file is already the original
                data = read_json(self.orig(t))
                idx = index_of(data)
                for rid, flds in rows.items():
                    if rid not in idx:
                        skipped.append(f"{t}:{rid}")
                        continue
                    for f, v in flds.items():
                        if isinstance(v, list):
                            # an empty list keeps the file's own "empty" shape ({} or [])
                            v = [list(x) for x in v] if v else (idx[rid].get(f) if idx[rid].get(f) in ({}, []) else [])
                        idx[rid][f] = v
                        fields += 1
                self._backup(t, stamp)
                atomic_write(self.live(t), compact(data) + "\n")
                st[t]["applied_sha"] = sha256(self.live(t))
                written.append(t + ".json")
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            overlay = self.write_overlay(ov)
            return {"rows": self.count(ov) - len(skipped), "fields": fields, "skippedRows": skipped,
                    "written": written, "backup": stamp, "overlay": overlay, "meta": self.meta()}

    def restore_original(self):
        with LOCK:
            self.check_home()
            st = self.ensure_originals()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for t in TABLES:
                self._backup(t, stamp)
                shutil.copy2(self.orig(t), self.live(t))
                st[t]["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            self.write_overlay({t: {} for t in TABLES})     # originals are back: nothing for the client to override
            return self.meta()

    def export_text(self):
        with LOCK:
            if self.cache is None:
                self.cache = self.load_game_data()
            names = {e["equipId"]: e for e in self.cache["equipment"]}
            by_item = {e["id"]: e for e in self.cache["equipment"]}
            attrs = self.cache["attrs"]
            lab = lambda a: attrs.get(str(a), {}).get("name", str(a))
            ov = self.load_overrides()
            lines = ["Item changes (old -> new)", ""]
            for rid, flds in sorted(ov["EquipConfig"].items(), key=lambda kv: int(kv[0])):
                e = names.get(rid)
                lines.append(f"{e['name'] if e else '?'} (item {e['id'] if e else '?'}, equip row {rid})")
                old = self.base["EquipConfig"].get(rid, {}).get("_kBasicAttribute", [])
                for f, v in flds.items():
                    fmt = lambda ls: ", ".join(f"{lab(a)} {lo}" + (f"-{hi}" if hi != lo else "") for a, lo, hi in ls) or "(none)"
                    lines.append(f"  - Base stats: {fmt(old)}  ->  {fmt(norm_lines(v))}")
            cards = {t["row"]: (c, t) for c in self.cache.get("cards", []) for t in c["tiers"]}
            for rid, flds in sorted(ov["CardConfig"].items(), key=lambda kv: int(kv[0])):
                c, t = cards.get(rid, ({"name": "?", "id": "?"}, {"tier": "?"}))
                lines.append(f"{c['name']} (card {c['id']}, star {t['tier']})")
                fmt = lambda ls: ", ".join(f"{lab(a)} {v}" for a, v in ls) or "(none)"
                for f, v in flds.items():
                    lines.append(f"  - Card stats: {fmt(self.base['CardConfig'][rid][f])}  ->  {fmt(norm_lines(v, 'pair'))}")
            for rid, flds in sorted(ov["CardUpgradeConfig"].items(), key=lambda kv: int(kv[0])):
                up = next(((k, u) for k, us in self.cache.get("upgrades", {}).items() for u in us if u["row"] == rid), None)
                lines.append(f"Card level-up table row {rid}" + (f" (slot:quality {up[0]}, level {up[1]['level']})" if up else ""))
                fmt = lambda ls: ", ".join(f"{lab(a)} {v}" for a, v in ls) or "(none)"
                for f, v in flds.items():
                    lines.append(f"  - Stats at this level: {fmt(self.base['CardUpgradeConfig'][rid][f])}  ->  {fmt(norm_lines(v, 'pair'))}")
            for rid, flds in sorted(ov["ItemConfig"].items(), key=lambda kv: int(kv[0])):
                e = by_item.get(rid)
                lines.append(f"{e['name'] if e else '?'} (item {rid})")
                for f, v in flds.items():
                    nice = {"_iLevelNeed": "Level needed", "_iEnchantSlot": "Enchant slots"}.get(f, f)
                    lines.append(f"  - {nice}: {self.base['ItemConfig'][rid][f]} -> {v}")
            return "\n".join(lines) + "\n"
