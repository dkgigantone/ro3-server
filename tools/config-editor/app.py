#!/usr/bin/env python3
"""RO3 Plus Config Editor - a small local web app for editing skill timing, items, shops, descriptions and monsters in RO3Emu.

Run:  py -3 app.py            (opens http://127.0.0.1:8765)
      py -3 app.py --home "C:\\path\\to\\RO3 OFFICIAL SERVER FILES V2" --port 8765

How it works
  * Your edits are stored in overrides/skills.json: only the fields you changed, per skill row.
    That file is small and readable, so it is the thing to commit to git.
  * "Apply to game" builds data/config/SkillConfig.json = untouched original + your overrides.
    The untouched original is copied once into state/originals/ (never committed).
  * The server reads data/config once at startup, so restart it after Apply.
Standard library only. Listens on 127.0.0.1 only.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from common import APP_DIR, LOCK, STATE_DIR, atomic_write, client_dir, compact, read_json, server_running, set_client_dir, sha256
from items import ItemsEditor
from levelpenalty import LevelPenaltyEditor
from mobs import MobsEditor
from spawns import SpawnEditor
from shops import ShopsEditor
from texts import TextsEditor

STATIC = APP_DIR / "static"
OVERRIDES = APP_DIR / "overrides" / "skills.json"
SETTINGS = STATE_DIR / "settings.json"
CONFIG_NAME = "SkillConfig.json"
MAX_BACKUPS = 5

# Fields the editor may change, with the allowed range (whole numbers, milliseconds unless noted).
FIELDS = {
    "_iChantVar": (0, 600_000),          # variable cast time (DEX shortens it)
    "_iChantFix": (0, 600_000),          # fixed cast time (DEX does not shorten it)
    "_iActionTime": (0, 600_000),        # animation / after-cast lock
    "_iActionTimeInfluence": (0, 100),   # percent of ASPD that speeds up the action time
    "_iCD": (0, 99_999_999),             # cooldown of this skill
}
LIST_FIELDS = {"_iPubCD"}                # shared (global) cooldown list, edited per entry


# ---------------------------------------------------------------- helpers
# ---------------------------------------------------------------- editor state
class Editor:
    def __init__(self, home: Path):
        self.home = home
        self.skills_cache = None
        self.base = None  # rowId -> only the editable fields of the untouched original (small, for validation)
        # Each game folder gets its own originals, backups and state, so switching folders never loses anything.
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.originals = self.state_dir / "originals"
        self.backups = self.state_dir / "backups"
        self.state_file = self.state_dir / "state.json"

    # paths
    @property
    def config_dir(self):
        return self.home / "data" / "config"

    @property
    def config_file(self):
        return self.config_dir / CONFIG_NAME

    @property
    def original_file(self):
        return self.originals / CONFIG_NAME

    def check_home(self):
        if not self.config_file.is_file():
            raise FileNotFoundError(
                f"Could not find {self.config_file}. Pick the RO3Emu folder that contains data\\config.")

    # state.json = {"original_sha": ..., "applied_sha": ...}
    def load_state(self):
        return read_json(self.state_file, {}) or {}

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def ensure_original(self):
        """Keep one untouched copy of SkillConfig.json. Taken from the live file the first time."""
        st = self.load_state()
        if self.original_file.is_file() and st.get("original_sha"):
            return st
        self.originals.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.config_file, self.original_file)
        st = {"original_sha": sha256(self.original_file), "applied_sha": None, "home": str(self.home)}
        self.save_state(st)
        return st

    def file_status(self):
        """pristine = live file is the original; applied = live file is what Apply wrote; external = changed elsewhere."""
        st = self.ensure_original()
        cur = sha256(self.config_file)
        if cur == st.get("original_sha"):
            return "pristine"
        if cur == st.get("applied_sha"):
            return "applied"
        return "external"

    def rebaseline(self):
        """Treat the live file as the new untouched original (after an upstream refresh)."""
        shutil.copy2(self.config_file, self.original_file)
        st = {"original_sha": sha256(self.original_file), "applied_sha": None, "home": str(self.home)}
        self.save_state(st)
        self.skills_cache = None
        self.base = None

    # overrides
    def load_overrides(self):
        data = read_json(OVERRIDES, {}) or {}
        return data.get("skills", {}) if isinstance(data, dict) else {}

    def save_overrides(self, skills):
        doc = {
            "_about": "Edits made with plus-config-editor. Only changed fields are stored. Row id = skill id * 100 + level.",
            "skills": {k: skills[k] for k in sorted(skills, key=int) if skills[k]},
        }
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    # data for the page
    def load_game_data(self):
        original = read_json(self.original_file)
        loc = read_json(self.config_dir / "Localization_en.json", {}) or {}
        jobs = read_json(self.config_dir / "JobConfig.json", {}) or {}
        params = read_json(self.config_dir / "PublicParamConfig.json", {}) or {}

        def text(ids):
            ids = ids if isinstance(ids, list) else [ids]
            for i in ids:
                row = loc.get(str(i))
                if isinstance(row, dict) and row.get("_kDes"):
                    return str(row["_kDes"])
            return ""

        job_names = {}
        for key, rec in jobs.items():
            if isinstance(rec, dict):
                label = text(rec.get("_iName", [])) or str(rec.get("_kUniqueName", "")).strip()
                if label:
                    job_names[str(rec.get("_iJob", key))] = label
        job_names["0"] = "Common / system skills"

        skills = {}
        row_names = {}
        base = {}
        for rid, row in original.items():
            if not isinstance(row, dict):
                continue
            base[rid] = {f: row.get(f, 0) for f in FIELDS}
            base[rid]["_iPubCD"] = list(row["_iPubCD"]) if isinstance(row.get("_iPubCD"), list) else None
            sid = str(row.get("_iSkillID", rid))
            name = text(row.get("_iName", [])) or f"(unnamed skill {sid})"
            row_names[rid] = name
            s = skills.setdefault(sid, {"id": sid, "name": name, "job": str(row.get("_iJob", 0)),
                                        "maxLevel": row.get("_iMaxLevel", 1), "levels": []})
            lvl = {"row": rid, "level": row.get("_iLevel", 1), "mode": row.get("_iMode", 0)}
            for f in FIELDS:
                lvl[f] = row.get(f, 0)
            pub = row.get("_iPubCD") if isinstance(row.get("_iPubCD"), list) else []
            grp = row.get("_iPubCDGroup") if isinstance(row.get("_iPubCDGroup"), list) else []
            lvl["pub"] = [{"target": grp[i] if i < len(grp) else None, "ms": pub[i]} for i in range(len(pub))]
            s["levels"].append(lvl)
        out = []
        for s in skills.values():
            s["levels"].sort(key=lambda l: (l["mode"], l["level"], int(l["row"])))
            s["modes"] = sorted({l["mode"] for l in s["levels"]})
            out.append(s)
        out.sort(key=lambda s: (s["name"].lower(), int(s["id"]) if s["id"].isdigit() else 0))
        # name of each shared-cooldown target (a row id or a skill id)
        target_names = {}
        for s in out:
            target_names[s["id"]] = s["name"]
        for rid, nm in row_names.items():
            target_names.setdefault(rid, nm)
        formula = {
            "A": params.get("VariableChantPerParamA", 3),
            "B": params.get("VariableChantPerParamB", 800),
            "C": params.get("VariableChantPerParamC", 1),
            "castCap": (params.get("FinalAtkSpdThdAndCastSpdThd") or [500, 400])[1],
            "aspdCap": (params.get("FinalAtkSpdThdAndCastSpdThd") or [500, 400])[0],
        }
        self.base = base
        return {"skills": out, "jobs": job_names, "formula": formula, "targetNames": target_names}

    def payload(self):
        with LOCK:
            self.check_home()
            self.ensure_original()
            if self.skills_cache is None:
                self.skills_cache = self.load_game_data()
            ov = self.load_overrides()
            data = dict(self.skills_cache)
            data["overrides"] = ov
            data["status"] = self.meta()
            return data

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True).encode("utf-8")).hexdigest()

    def meta(self):
        with LOCK:
            status = self.file_status()
            ov = self.load_overrides()
            st = self.load_state()
            return {
                "home": str(self.home),
                "fileStatus": status,
                "inSync": status == "applied" and st.get("applied_overrides") == self.ov_hash(ov),
                "serverRunning": server_running(),
                "changedRows": len(self.load_overrides()),
                "overridesFile": str(OVERRIDES),
            }

    # editing
    def _edit(self, base, ov, row, field, value, index=None):
        """Change one value inside the in-memory override dict (validated against the untouched original)."""
        if row not in base:
            raise ValueError(f"Skill row {row} does not exist.")
        entry = ov.setdefault(row, {})
        orig = base[row]
        if field in FIELDS:
            if value is None:
                entry.pop(field, None)
            else:
                lo, hi = FIELDS[field]
                if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                    raise ValueError(f"{field} must be a whole number between {lo} and {hi}.")
                if value == orig[field]:
                    entry.pop(field, None)
                else:
                    entry[field] = value
        elif field in LIST_FIELDS:
            lst = orig.get(field)
            if not isinstance(lst, list) or not isinstance(index, int) or not 0 <= index < len(lst):
                raise ValueError("That shared cooldown entry does not exist.")
            cur = list(entry.get(field, lst))
            if value is None:
                cur[index] = lst[index]
            else:
                if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 99_999_999:
                    raise ValueError("Shared cooldown must be a whole number of milliseconds (0 - 99,999,999).")
                cur[index] = value
            if cur == lst:
                entry.pop(field, None)
            else:
                entry[field] = cur
        else:
            raise ValueError(f"Field {field} cannot be edited here.")
        if not entry:
            ov.pop(row, None)

    def set_many(self, items):
        """All-or-nothing: every item is checked first; the overrides file is written once."""
        with LOCK:
            self.check_home()
            self.ensure_original()
            if self.base is None:
                self.skills_cache = self.load_game_data()
            ov = self.load_overrides()
            for it in items:
                self._edit(self.base, ov, str(it["row"]), it["field"], it.get("value"), it.get("index"))
            self.save_overrides(ov)
            return self.meta()

    def set_field(self, row, field, value, index=None):
        return self.set_many([{"row": row, "field": field, "value": value, "index": index}])

    def reset_all(self):
        with LOCK:
            self.save_overrides({})
            return self.meta()

    def apply(self):
        with LOCK:
            self.check_home()
            st = self.ensure_original()
            status = self.file_status()
            if status == "external":
                raise RuntimeError("The config file was changed outside this tool. Use 'Use current file as new original' first.")
            original = read_json(self.original_file)
            ov = self.load_overrides()
            missing = [r for r in ov if r not in original]
            changed = 0
            for rid, fields in ov.items():
                if rid not in original:
                    continue
                for f, v in fields.items():
                    if f in FIELDS or f in LIST_FIELDS:
                        original[rid][f] = v
                        changed += 1
            self.backups.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.config_file, self.backups / f"SkillConfig-{stamp}.json")
            for old in sorted(self.backups.glob("SkillConfig-*.json"))[:-MAX_BACKUPS]:
                old.unlink(missing_ok=True)
            atomic_write(self.config_file, compact(original) + "\n")
            st["applied_sha"] = sha256(self.config_file)
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            return {"rows": len(ov) - len(missing), "fields": changed, "skippedRows": missing,
                    "backup": f"SkillConfig-{stamp}.json", "meta": self.meta()}

    def restore_original(self):
        """Put the untouched original back in the game folder (keeps your overrides file)."""
        with LOCK:
            self.check_home()
            st = self.ensure_original()
            self.backups.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.config_file, self.backups / f"SkillConfig-{stamp}.json")
            shutil.copy2(self.original_file, self.config_file)
            st["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            return self.meta()

    def export_text(self):
        with LOCK:
            data = self.skills_cache or self.load_game_data()
            names = {l["row"]: (s["name"], l["level"]) for s in data["skills"] for l in s["levels"]}
            original = read_json(self.original_file)
            lines = ["Skill timing changes (old -> new)", ""]
            labels = {"_iChantVar": "Variable cast", "_iChantFix": "Fixed cast", "_iActionTime": "Action time",
                      "_iActionTimeInfluence": "ASPD influence %", "_iCD": "Cooldown", "_iPubCD": "Shared cooldown"}
            for rid, fields in sorted(self.load_overrides().items(), key=lambda kv: int(kv[0])):
                nm, lv = names.get(rid, ("?", "?"))
                lines.append(f"{nm} (row {rid}, Lv {lv})")
                for f, v in fields.items():
                    lines.append(f"  - {labels.get(f, f)}: {original[rid].get(f)} -> {v}")
            return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    editor: Editor = None
    items: ItemsEditor = None
    shops: ShopsEditor = None
    texts: TextsEditor = None
    mobs: MobsEditor = None
    spawns: SpawnEditor = None
    levelpenalty: LevelPenaltyEditor = None

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def ok(self, obj):
        self.send(200, json.dumps(obj, ensure_ascii=False))

    def fail(self, code, msg):
        self.send(code, json.dumps({"error": str(msg)}))

    def local_only(self):
        # Only the page served by this app may call the API (blocks other websites from posting to localhost).
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        return True

    def do_GET(self):
        if not self.local_only():
            return self.fail(403, "Forbidden")
        path = urlparse(self.path).path
        try:
            if path == "/favicon.ico":
                return self.send(204, b"", "image/x-icon")
            if path in ("/", "/index.html"):
                return self.send(200, (STATIC / "index.html").read_bytes(), "text/html")
            if path == "/items":
                return self.send(200, (STATIC / "items.html").read_bytes(), "text/html")
            if path == "/shops":
                return self.send(200, (STATIC / "shops.html").read_bytes(), "text/html")
            if path == "/texts":
                return self.send(200, (STATIC / "texts.html").read_bytes(), "text/html")
            if path == "/mobs":
                return self.send(200, (STATIC / "mobs.html").read_bytes(), "text/html")
            if path == "/levelpenalty":
                return self.send(200, (STATIC / "levelpenalty.html").read_bytes(), "text/html")
            if path == "/api/levelpenalty/get":
                return self.ok(self.levelpenalty.get())
            if path == "/api/mobs/meta":
                return self.ok(self.mobs.meta())
            if path == "/api/mobs/search":
                from urllib.parse import parse_qs
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                return self.ok(self.mobs.search(q.get("q", ""), int(q.get("lo") or 0), int(q.get("hi") or 0), q.get("cls", "all"),
                                                int(q.get("race", -1)), int(q.get("size", -1)), int(q.get("element", -1)),
                                                q.get("sort", "level"), q.get("desc") == "1", int(q.get("page") or 1), int(q.get("limit") or 100),
                                                q.get("unnamed") == "1"))
            if path == "/api/mobs/items":
                from urllib.parse import parse_qs
                return self.ok(self.mobs.item_search((parse_qs(urlparse(self.path).query).get("q") or [""])[0]))
            if path == "/api/spawns/meta":
                return self.ok(self.spawns.meta())
            if path == "/api/spawns/get":
                from urllib.parse import parse_qs
                return self.ok(self.spawns.get(int((parse_qs(urlparse(self.path).query).get("id") or ["0"])[0])))
            if path == "/api/mobs/get":
                from urllib.parse import parse_qs
                return self.ok(self.mobs.get(int((parse_qs(urlparse(self.path).query).get("id") or ["0"])[0])))
            if path == "/api/texts/search":
                from urllib.parse import parse_qs
                return self.ok(self.texts.search((parse_qs(urlparse(self.path).query).get("q") or [""])[0]))
            if path == "/api/texts/skills":
                from urllib.parse import parse_qs
                return self.ok(self.texts.search_skills((parse_qs(urlparse(self.path).query).get("q") or [""])[0]))
            if path == "/api/texts/suggest":
                from urllib.parse import parse_qs
                return self.ok(self.texts.suggest((parse_qs(urlparse(self.path).query).get("item") or ["0"])[0]))
            if path == "/api/client-dir":
                c = client_dir()
                return self.ok({"dir": str(c) if c else ""})
            if path == "/api/shops/data":
                return self.ok(self.shops.payload())
            if path == "/api/shops/export.csv":
                return self.send(200, self.shops.export_csv(), "text/csv")
            if path == "/api/items/data":
                return self.ok(self.items.payload())
            if path == "/api/items/state":
                return self.ok({"overrides": self.items.load_overrides(), "status": self.items.meta()})
            if path == "/api/items/export":
                return self.send(200, self.items.export_text(), "text/plain")
            if path == "/api/data":
                return self.ok(self.editor.payload())
            if path == "/api/meta":
                return self.ok(self.editor.meta())
            if path == "/api/state":
                return self.ok({"overrides": self.editor.load_overrides(), "status": self.editor.meta()})
            if path == "/api/export":
                return self.send(200, self.editor.export_text(), "text/plain")
            self.fail(404, "Not found")
        except Exception as exc:
            self.fail(500, exc)

    def do_POST(self):
        if not self.local_only():
            return self.fail(403, "Forbidden")
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            ed = self.editor
            if path == "/api/set":
                return self.ok(ed.set_field(str(body["row"]), body["field"], body.get("value"), body.get("index")))
            if path == "/api/set-many":
                return self.ok(ed.set_many(body["items"]))
            if path == "/api/apply":
                return self.ok(ed.apply())
            if path == "/api/reset-all":
                return self.ok(ed.reset_all())
            if path == "/api/restore-original":
                return self.ok(ed.restore_original())
            if path == "/api/rebaseline":
                ed.rebaseline()
                return self.ok(ed.meta())
            if path == "/api/client-dir":
                return self.ok({"dir": str(set_client_dir(body["dir"]))})
            if path == "/api/shops/set-many":
                return self.ok(self.shops.set_many(body["items"]))
            if path == "/api/texts/set":
                return self.ok(self.texts.set_item(body["item"], body.get("text", "")))
            if path == "/api/texts/skill-set":
                return self.ok(self.texts.set_skill(body["id"], body.get("text", "")))
            if path == "/api/texts/skill-reset":
                return self.ok(self.texts.reset_skill(body["id"]))
            if path == "/api/texts/reset":
                return self.ok(self.texts.reset_item(body["item"]))
            if path == "/api/texts/apply":
                return self.ok(self.texts.apply())
            if path == "/api/shops/add-item":
                return self.ok(self.shops.add_item(body["cls"], body["itemId"], body["price"], body.get("cur"),
                                                   body.get("num", 0), body.get("per", 0)))
            if path == "/api/shops/remove-added":
                return self.ok(self.shops.remove_added(body["row"]))
            if path == "/api/shops/item-info":
                return self.ok(self.shops.item_info(body["itemId"]))
            if path == "/api/shops/apply":
                return self.ok(self.shops.apply())
            if path == "/api/shops/reset-all":
                return self.ok(self.shops.reset_all())
            if path == "/api/shops/restore-original":
                return self.ok(self.shops.restore_original())
            if path == "/api/shops/rebaseline":
                self.shops.rebaseline()
                return self.ok(self.shops.meta())
            if path == "/api/shops/import-preview":
                return self.ok(self.shops.import_preview(body["csv"]))
            if path == "/api/shops/import-apply":
                return self.ok(self.shops.import_apply(body["csv"]))
            if path == "/api/items/set-many":
                return self.ok(self.items.set_many(body["items"]))
            if path == "/api/items/apply":
                return self.ok(self.items.apply())
            if path == "/api/items/reset-all":
                return self.ok(self.items.reset_all())
            if path == "/api/items/restore-original":
                return self.ok(self.items.restore_original())
            if path == "/api/items/rebaseline":
                self.items.rebaseline()
                return self.ok(self.items.meta())
            if path == "/api/mobs/set":
                return self.ok(self.mobs.set_fields(body["id"], body["values"]))
            if path == "/api/mobs/drops":
                return self.ok(self.mobs.set_drops(body["id"], body.get("drops")))
            if path == "/api/mobs/reset":
                return self.ok(self.mobs.reset_mob(body["id"]))
            if path == "/api/mobs/reset-all":
                return self.ok(self.mobs.reset_all())
            if path == "/api/mobs/apply":
                return self.ok(self.mobs.apply())
            if path == "/api/mobs/restore-original":
                return self.ok(self.mobs.restore_original())
            if path == "/api/spawns/set":
                return self.ok(self.spawns.set_spawns(body["id"], body.get("pools") or [], body.get("fixed") or []))
            if path == "/api/spawns/reset":
                return self.ok(self.spawns.reset_npc(body["id"]))
            if path == "/api/spawns/reset-all":
                return self.ok(self.spawns.reset_all())
            if path == "/api/spawns/apply":
                return self.ok(self.spawns.apply())
            if path == "/api/spawns/restore-original":
                return self.ok(self.spawns.restore_original())
            if path == "/api/spawns/rebaseline":
                self.spawns.rebaseline()
                return self.ok(self.spawns.meta())
            if path == "/api/mobs/rebaseline":
                self.mobs.rebaseline()
                return self.ok(self.mobs.meta())
            if path == "/api/levelpenalty/set":
                return self.ok(self.levelpenalty.set_steps(body["kind"], body.get("plan"), body.get("steps")))
            if path == "/api/levelpenalty/preview":
                return self.ok(self.levelpenalty.preview(body["kind"], body.get("plan"), body.get("steps")))
            if path == "/api/levelpenalty/check":
                return self.ok(self.levelpenalty.check(body["kind"], body.get("plan"), body.get("steps"), body["player"], body["monster"]))
            if path == "/api/levelpenalty/reset":
                return self.ok(self.levelpenalty.reset(body["kind"], body.get("plan")))
            if path == "/api/levelpenalty/reset-all":
                return self.ok(self.levelpenalty.reset_all())
            if path == "/api/levelpenalty/apply":
                return self.ok(self.levelpenalty.apply())
            if path == "/api/levelpenalty/restore-original":
                return self.ok(self.levelpenalty.restore_original())
            if path == "/api/levelpenalty/rebaseline":
                self.levelpenalty.rebaseline()
                return self.ok(self.levelpenalty.meta())
            if path == "/api/set-home":
                home = Path(body["home"])
                new = Editor(home)
                new.check_home()
                Handler.editor = new
                Handler.items = ItemsEditor(home)
                Handler.shops = ShopsEditor(home)
                Handler.texts = TextsEditor(home)
                Handler.mobs = MobsEditor(home)
                Handler.spawns = SpawnEditor(home)
                Handler.levelpenalty = LevelPenaltyEditor(home)
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                atomic_write(SETTINGS, json.dumps({"home": str(home)}))
                return self.ok(new.meta())
            self.fail(404, "Not found")
        except (ValueError, FileNotFoundError, RuntimeError, KeyError) as exc:
            self.fail(400, exc)
        except Exception as exc:
            self.fail(500, exc)


def guess_home(arg):
    if arg:
        return Path(arg)
    saved = read_json(SETTINGS, {}) or {}
    if saved.get("home"):
        return Path(saved["home"])
    for cand in (APP_DIR.parent.parent, APP_DIR.parent, Path(r"C:\Users\donor\Documents\RO3-Studio\RO3 OFFICIAL SERVER FILES V2")):
        if (cand / "data" / "config" / CONFIG_NAME).is_file():
            return cand
    return APP_DIR.parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", help="RO3Emu folder that contains data\\config")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    Handler.editor = Editor(guess_home(args.home))
    Handler.items = ItemsEditor(Handler.editor.home)
    Handler.shops = ShopsEditor(Handler.editor.home)
    Handler.texts = TextsEditor(Handler.editor.home)
    Handler.mobs = MobsEditor(Handler.editor.home)
    Handler.spawns = SpawnEditor(Handler.editor.home)
    Handler.levelpenalty = LevelPenaltyEditor(Handler.editor.home)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"RO3 Plus Config Editor\n  game folder: {Handler.editor.home}\n  open: {url}\n  (close this window to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
