"""Descriptions page of the RO3 Plus Config Editor: change the text the game shows for an item.

The client draws its own text; the server's Localization_en.json is not what the item tooltip shows. So an edited
description is written to the client overlay (client-overlay/config-overlay.tsv, copied to <client>\\ro3emu\\custom\\):
  * Text <id> @text <english>           the text for a text id (answered by the hook's Language.Trans wrapper)
  * ItemConfig <item> _iDescription <id> only for an item that has no description yet (it is given a new text id)
An item that already has a description keeps its text id; only that text is replaced (so every item sharing that
text id shows the new words). Nothing on the server changes; restart the client to see an edit.
Your edits live in overrides/texts.json (commit it).
"""
import json
import re

from common import APP_DIR, LOCK, atomic_write, compact, read_json, update_overlay

OVERRIDES = APP_DIR / "overrides" / "texts.json"
ITEMS = "Items"
SKILLS = "Skills"
MAX_SKILL_TEXT = 3000
PLACEHOLDER = re.compile(r"\$\{\d+\}|\^\{\d+\}")
MAX_TEXT = 1500
NEW_ID_BASE = 99_920_000_000        # own text ids: base + item id (the custom hats use 999100000xx)


def _index(data):
    if isinstance(data, dict):
        return data
    return {str(r["_iID"]): r for r in data if isinstance(r, dict) and "_iID" in r}


UNLOCKS = {"200": "an appearance", "201": "a mount", "202": "an emoji", "212": "an emoji set"}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _esc(text):
    return text.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


class TextsEditor:
    def __init__(self, home):
        self.home = home
        self.items = None
        self.loc = None
        self.skill_groups = None
        self.skill_row_text = {}

    @property
    def config_dir(self):
        return self.home / "data" / "config"

    def _load(self):
        if self.items is None:
            path = self.config_dir / "ItemConfig.json"
            if not path.is_file():
                raise FileNotFoundError(f"Could not find {path}. Pick the RO3Emu folder that contains data\\config.")
            self.items = _index(read_json(path, {}))
            self.loc = _index(read_json(self.config_dir / "Localization_en.json", {}) or {})
            self.buffs = _index(read_json(self.config_dir / "BuffConfig.json", {}) or {})
            self.shared = {}
            for r in self.items.values():
                d = self._desc_id(r)
                if d:
                    self.shared[d] = self.shared.get(d, 0) + 1

    @staticmethod
    def _desc_id(row):
        d = row.get("_iDescription")
        try:
            return int(d[0]) if isinstance(d, list) and d and int(d[0]) > 0 else 0
        except (TypeError, ValueError):
            return 0

    def _text(self, tid):
        r = self.loc.get(str(tid))
        return r.get("_kDes", "") if isinstance(r, dict) else ""

    def _fmt(self, ids):
        """Text of a text-id list [id, arg1, arg2, ...] with @{n} / ${n} filled in; '' when unknown."""
        if not isinstance(ids, list) or not ids:
            return ""
        out = self._text(ids[0])
        if not out:
            return ""
        for n, arg in enumerate(ids[1:], start=1):
            out = out.replace("@{%d}" % n, str(arg)).replace("${%d}" % n, str(arg))
        return re.sub(r"[@$]\{\d+\}", "", out).strip()

    def suggest(self, item_id):
        """A starting text for an item's description, built from its real data (effect, cooldown, level).
        Returns {"text": str, "notes": [..]}; text is '' when there is nothing to build it from."""
        with LOCK:
            self._load()
            r = self.items.get(str(int(item_id)))
            if r is None:
                raise ValueError(f"Item {item_id} does not exist in ItemConfig.")
            lines, notes = [], []
            for e in r.get("_iUseEffect") or []:
                if not e or e[0] in ("", None):
                    continue
                a, args = str(e[0]), [str(x) for x in e[1:]]
                if a == "100" and len(args) >= 3:
                    what = {"1": "HP", "2": "SP"}.get(args[0])
                    flat, pct = _num(args[1]), _num(args[2]) / 100
                    parts = ([f"{flat:g} {what}"] if flat > 0 else []) + ([f"{pct:g}% of Max {what}"] if pct > 0 else [])
                    if what and parts:
                        lines.append("Restores " + " plus ".join(parts) + " when used.")
                    else:
                        notes.append(f"Could not read the restore effect (args {', '.join(args)}).")
                elif a in ("203", "10") and args:
                    b = self.buffs.get(args[0])
                    text = self._fmt(b.get("_iDescription")) if isinstance(b, dict) else ""
                    name = self._fmt(b.get("_iName")) if isinstance(b, dict) else ""
                    if text:
                        lines.append(text if text.endswith((".", "!")) else text + ".")
                    elif name:
                        lines.append(f"Gives the {name} effect.")
                    else:
                        notes.append(f"Buff {args[0]} has no English text.")
                elif a in UNLOCKS:
                    lines.append(f"Unlocks {UNLOCKS[a]}.")
                elif a == "105":
                    lines.append("Shouts a message to everyone in the world.")
                else:
                    notes.append(f"Effect type {a} is not supported yet: describe it by hand.")
            if lines:
                cd = _num(r.get("_iCD")) / 1000
                if cd > 0:
                    lines.append(f"Cooldown: {cd:g} sec.")
                lv = int(_num(r.get("_iLevelNeed")))
                if lv > 1:
                    lines.append(f"Required level: {lv}.")
            elif not notes:
                notes.append("This item has no use effect, so there is nothing to build a description from. Write it by hand.")
            return {"text": " ".join(lines[:1]) + ("\n" + " ".join(lines[1:]) if len(lines) > 1 else ""), "notes": notes}

    # ---- skill descriptions -------------------------------------------------------------------------------
    # One text template per skill (shared by every level row and often by a few skills). The client fills in
    # ${n} from the level row's _kDescData and ^{n} from the colour tags in its _iDescription, so only the
    # template is edited here; the hook (ConfigOverlay.Trans) does the filling for our own text.

    def _load_skills(self):
        if self.skill_groups is not None:
            return
        self._load()
        path = self.config_dir / "SkillConfig.json"
        if not path.is_file():
            raise FileNotFoundError(f"Could not find {path}. Pick the RO3Emu folder that contains data\\config.")
        data = _index(read_json(path, {}))
        groups, row_text = {}, {}
        for sid, r in data.items():
            if not isinstance(r, dict):
                continue
            d = self._desc_id(r)
            if not d:
                continue
            name = self._fmt(r.get("_iName")) or "(no English name)"
            g = groups.setdefault(d, {"rows": [], "names": {}})
            g["names"][name] = g["names"].get(name, 0) + 1
            desc = r.get("_iDescription") or []
            g["rows"].append({"id": int(sid), "level": r.get("_iLevel", 0), "name": name,
                              "data": [str(x) for x in (r.get("_kDescData") or [])],
                              "params": [str(x) for x in desc[1:]]})
            row_text[str(sid)] = d
        for g in groups.values():
            g["rows"].sort(key=lambda x: x["id"])
        self.skill_groups, self.skill_row_text = groups, row_text

    def _skill_card(self, tid, ov):
        g = self.skill_groups[tid]
        shipped = self._text(tid)
        mine = ov[SKILLS].get(str(tid))
        names = sorted(g["names"], key=lambda n: -g["names"][n])
        return {"textId": tid, "name": names[0], "otherNames": names[1:6], "skills": len(names), "rowCount": len(g["rows"]),
                "shipped": shipped, "text": mine if mine is not None else shipped, "edited": mine is not None,
                "rows": g["rows"][:60]}

    def search_skills(self, q):
        with LOCK:
            self._load_skills()
            q = (q or "").strip().lower()
            ov = self.load_overrides()
            tids = []
            if q:
                if q.isdigit():
                    if q in self.skill_row_text:
                        tids.append(self.skill_row_text[q])
                    if int(q) in self.skill_groups and int(q) not in tids:
                        tids.append(int(q))
                for tid, g in self.skill_groups.items():
                    if len(tids) >= 40:
                        break
                    if tid not in tids and any(q in n.lower() for n in g["names"]):
                        tids.append(tid)
            else:
                tids = [int(t) for t in sorted(ov[SKILLS], key=int) if int(t) in self.skill_groups]
            return {"rows": [self._skill_card(t, ov) for t in tids[:40]],
                    "edited": len(ov[ITEMS]) + len(ov[SKILLS]), "overlayFile": str(OVERRIDES)}

    def set_skill(self, text_id, text):
        with LOCK:
            self._load_skills()
            tid = int(text_id)
            if tid not in self.skill_groups:
                raise ValueError(f"Text id {tid} is not a skill description in SkillConfig.")
            ov = self.load_overrides()
            text = (text or "").replace("\r\n", "\n").strip()
            warnings = []
            if not text:
                ov[SKILLS].pop(str(tid), None)
            else:
                if len(text) > MAX_SKILL_TEXT:
                    raise ValueError(f"The description is too long (max {MAX_SKILL_TEXT} characters).")
                ov[SKILLS][str(tid)] = text
                missing = sorted(set(PLACEHOLDER.findall(self._text(tid))) - set(PLACEHOLDER.findall(text)))
                if missing:
                    warnings.append("The original uses " + ", ".join(missing) + " which your text does not: those numbers or colours will not show.")
            self._save(ov)
            result = self.apply()
            result["warnings"] = warnings
            return result

    def reset_skill(self, text_id):
        return self.set_skill(text_id, "")

    def load_overrides(self):
        ov = read_json(OVERRIDES, {}) or {}
        ov.setdefault(ITEMS, {})
        ov.setdefault(SKILLS, {})
        return ov

    def _save(self, ov):
        OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(OVERRIDES, json.dumps(ov, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    def _row(self, item_id, ov=None):
        r = self.items.get(str(item_id))
        if r is None:
            return None
        ov = ov or self.load_overrides()
        d = self._desc_id(r)
        shipped = self._text(d) if d else ""
        mine = ov[ITEMS].get(str(item_id))
        return {"id": int(item_id), "name": self._text((r.get("_iName") or [0])[0]), "hasDesc": bool(d), "descId": d,
                "shipped": shipped, "text": mine if mine is not None else shipped, "edited": mine is not None,
                "sharedBy": (self.shared.get(d, 1) - 1) if d else 0}

    def search(self, q):
        with LOCK:
            self._load()
            q = (q or "").strip().lower()
            ov = self.load_overrides()
            out = []
            if q:
                ids = [q] if q.isdigit() and q in self.items else []
                for iid, r in self.items.items():
                    if len(out) + len(ids) >= 40:
                        break
                    if iid in ids:
                        continue
                    nm = self._text((r.get("_iName") or [0])[0]).lower()
                    if q in nm or (q.isdigit() and iid.startswith(q)):
                        ids.append(iid)
                out = [self._row(i, ov) for i in ids[:40]]
            else:
                out = [self._row(i, ov) for i in sorted(ov[ITEMS], key=int)]
            return {"rows": [r for r in out if r], "edited": len(ov[ITEMS]) + len(ov[SKILLS]), "overlayFile": str(OVERRIDES)}

    def set_item(self, item_id, text):
        with LOCK:
            self._load()
            iid = str(int(item_id))
            if iid not in self.items:
                raise ValueError(f"Item {iid} does not exist in ItemConfig.")
            ov = self.load_overrides()
            text = (text or "").replace("\r\n", "\n").strip()
            if not text:
                ov[ITEMS].pop(iid, None)
            else:
                if len(text) > MAX_TEXT:
                    raise ValueError(f"The description is too long (max {MAX_TEXT} characters).")
                ov[ITEMS][iid] = text
            self._save(ov)
            return self.apply()

    def reset_item(self, item_id):
        return self.set_item(item_id, "")

    def overlay_lines(self, ov):
        lines, seen = [], set()
        for iid, text in sorted(ov[ITEMS].items(), key=lambda kv: int(kv[0])):
            r = self.items.get(iid)
            if r is None:
                continue
            d = self._desc_id(r)
            if not d:
                d = NEW_ID_BASE + int(iid)
                lines.append("\t".join(["ItemConfig", iid, "_iDescription", str(d)]))
            if d not in seen:
                seen.add(d)
                lines.append("\t".join(["Text", str(d), "@text", _esc(text)]))
        for tid, text in sorted(ov[SKILLS].items(), key=lambda kv: int(kv[0])):
            if int(tid) not in seen:
                seen.add(int(tid))
                lines.append("\t".join(["Text", tid, "@text", _esc(text)]))
        return lines

    def apply(self):
        """Writes the overlay lines of every edited description and copies the file into the client."""
        with LOCK:
            self._load()
            ov = self.load_overrides()
            update_overlay({"ItemConfig", "Text"}, self.overlay_lines(ov))
            from common import LAST_PUBLISH
            return {"edited": len(ov[ITEMS]) + len(ov[SKILLS]), "published": dict(LAST_PUBLISH)}
