#!/usr/bin/env python3
"""RO3 Eclipse Custom Items Editor: a small local web app for our custom wardrobe items (hats, face items ...).

Run:  py -3 app.py            (opens http://127.0.0.1:8766)
      py -3 app.py --client "C:\\Games\\Ragnarok3-Official" --port 8766

What it does (it drives the tools that already exist next to it, in ..\\custom-items):
  * Items: edit name, rarity, item ID ... of every custom item (writes custom-items.csv, the one source list) and add new ones.
  * Build: runs build_custom_items.py (server tables, client items.tsv, aliases). "Preview" runs it with --check (writes nothing).
  * Import: starts import_hat.py in its own window for an item that comes from the Origin game files.
  * Live tuning: the sliders of the old HatStudio (size, rotation, position ...) write <client>\\ro3emu\\hats\\<Key>M/F.txt
    at once; the running game picks the change up by itself.
  * Run: builds, then starts "RUN RO3 DEV SERVER.bat" (server + game) and shows the live tuning page.
Standard library only. Listens on 127.0.0.1 only.
"""
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP = Path(__file__).resolve().parent
TOOLS = APP.parent / "custom-items"          # customs\tools\custom-items
REPO = APP.parents[2]                        # customs\tools\custom-items-editor -> the V2 repository root
STATE = APP / "state"
STATIC = APP / "static"
SETTINGS = STATE / "settings.json"
CSV_PATH = TOOLS / "custom-items.csv"
DEFAULT_CLIENT = r"C:\Games\Ragnarok3-Official"
LOCK = threading.RLock()

sys.path.insert(0, str(TOOLS))
import build_custom_items as B  # noqa: E402  (the slot table, ID ranges and rarity values the build uses)

RARITIES = ["Normal", "Advanced", "Collector's", "Luxury"]
SLOT_LABEL = {"head": "Hat (head)", "face": "Face", "mouth": "Mouth", "back": "Back"}
FIELDS = ["Key", "ItemName", "Description", "StyleBonus", "Rarity", "Stall", "StallPrice", "OriginHat",
          "ItemID", "WardrobeID", "RenamedFrom", "Slot"]
ALIAS_SUFFIXES = B.SUFFIXES
SUBPROCESS_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
NEW_CONSOLE_WIN = os.name == "nt"


# ---------------------------------------------------------------- settings / client

def read_settings():
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_settings(data):
    STATE.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data), encoding="utf-8")


class Ctx:
    client = None


def client_dir():
    return Path(Ctx.client)


def set_client(path):
    p = Path(str(path).strip().strip('"'))
    if not (p / "ro3emu").is_dir():
        raise ValueError(f"{p} has no ro3emu folder (use the game folder, e.g. {DEFAULT_CLIENT}).")
    Ctx.client = str(p)
    data = read_settings()
    data["client"] = str(p)
    write_settings(data)
    return p


# ---------------------------------------------------------------- the CSV (custom-items.csv)

def read_csv():
    raw = CSV_PATH.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    names = list(reader.fieldnames or [])
    for f in FIELDS:
        if f not in names:
            names.append(f)
    rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return rows, names, bom


def write_csv(rows, names, bom):
    """Keeps the last 20 copies in state/backups, then writes the file (temp file + replace)."""
    STATE.mkdir(parents=True, exist_ok=True)
    backups = STATE / "backups"
    backups.mkdir(exist_ok=True)
    if CSV_PATH.is_file():
        shutil.copyfile(CSV_PATH, backups / ("custom-items-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".csv"))
        for old in sorted(backups.glob("custom-items-*.csv"))[:-20]:
            old.unlink()
    tmp = CSV_PATH.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
        w = csv.DictWriter(f, fieldnames=names, lineterminator="\r\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CSV_PATH)


def slot_of(row):
    return (row.get("Slot") or "head").strip().lower() or "head"


def truthy(value):
    return str(value).strip().lower() in ("true", "yes", "1")


def rarity_name(text):
    low = str(text or "").strip().lower()
    for name in RARITIES:
        if name.lower() == low or (name == "Collector's" and low == "collectors"):
            return name
    return None


def to_int(value, what, lo=None, hi=None):
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{what} must be a whole number (got '{value}').")
    if (lo is not None and number < lo) or (hi is not None and number > hi):
        raise ValueError(f"{what} must be between {lo} and {hi} (got {number}).")
    return number


def clean_fields(data, rows, current_key=None):
    """Checks one item the way build_custom_items.py does and returns the CSV values; raises ValueError with a plain message."""
    out = {}
    slot = (data.get("Slot") or "head").strip().lower()
    if slot not in B.SLOTS:
        raise ValueError(f"Slot must be one of {', '.join(B.SLOTS)}.")
    cfg = B.SLOTS[slot]
    name = (data.get("ItemName") or "").strip()
    if not name or len(name) > 60 or any(c in name for c in "\t\r\n"):
        raise ValueError("The item name must be 1-60 characters, one line.")
    desc = (data.get("Description") or "").strip() or "A custom item."
    if any(c in desc for c in "\t\r\n"):
        raise ValueError("The description must be one line.")
    rarity = rarity_name(data.get("Rarity"))
    if rarity is None:
        raise ValueError("Rarity must be Normal, Advanced, Collector's or Luxury.")
    stall = truthy(data.get("Stall"))
    style = to_int(data.get("StyleBonus"), "Style bonus", 10, 1000)
    price = (str(data.get("StallPrice") or "")).strip()
    price_n = to_int(price, "Stall price", 1, 999999999) if (price or stall) else 0
    item_id = to_int(data.get("ItemID"), "Item ID", B.ITEM_FIRST, B.ITEM_LAST)
    ward_id = to_int(data.get("WardrobeID"), "Wardrobe ID", cfg["ward_first"], cfg["ward_last"])
    for r in rows:
        if r["Key"] == current_key:
            continue
        if to_int(r.get("ItemID") or 0, "x") == item_id:
            raise ValueError(f"Item ID {item_id} is already used by {r['ItemName']}.")
        if (r.get("WardrobeID") or "").strip() and int(r["WardrobeID"]) == ward_id:
            raise ValueError(f"Wardrobe ID {ward_id} is already used by {r['ItemName']}.")
    out.update({"ItemName": name, "Description": desc, "StyleBonus": str(style), "Rarity": rarity,
                "Stall": "TRUE" if stall else "FALSE", "StallPrice": str(price_n) if price_n else "",
                "OriginHat": (data.get("OriginHat") or "").strip(), "ItemID": str(item_id), "WardrobeID": str(ward_id),
                "Slot": slot})
    return out


def camel(english, taken):
    words = re.findall(r"[A-Za-z0-9]+", english)
    key = "".join(w[:1].upper() + w[1:] for w in words) or "Item"
    if not key[0].isalpha():
        key = "Item" + key
    key = key[:40]
    base, n = key, 2
    while key.lower() in taken:
        key = base + str(n)
        n += 1
    return key


def next_ids(rows, slot):
    cfg = B.SLOTS[slot]
    used_items = {int(r["ItemID"]) for r in rows if (r.get("ItemID") or "").strip()}
    used_wards = {int(r["WardrobeID"]) for r in rows if (r.get("WardrobeID") or "").strip()}
    item = next((i for i in range(B.ITEM_FIRST, B.ITEM_LAST + 1) if i not in used_items), None)
    ward = next((i for i in range(cfg["ward_first"], cfg["ward_last"] + 1) if i not in used_wards), None)
    return item, ward


# ---------------------------------------------------------------- what is in place for an item

_JSON_CACHE = {}


def server_table_keys(name):
    path = REPO / "data" / "config" / (name + ".json")
    try:
        stt = path.stat()
    except OSError:
        return None
    key = (str(path), stt.st_mtime_ns, stt.st_size)
    if key not in _JSON_CACHE:
        _JSON_CACHE.clear()
        _JSON_CACHE[key] = set(json.loads(path.read_text(encoding="utf-8-sig")).keys())
    return _JSON_CACHE[key]


def names_in(folder):
    try:
        return {n.lower() for n in os.listdir(folder)}
    except OSError:
        return set()


def check_item(row, ctx):
    """The steps an item needs, each ok or not, with a plain hint what to do."""
    slot = slot_of(row)
    cfg = B.SLOTS.get(slot, B.SLOTS["head"])
    key, item_id, ward_id = row["Key"], row.get("ItemID", ""), row.get("WardrobeID", "")
    asset = key.lower()
    checks = []

    def add(name, ok, detail, hint=""):
        checks.append({"name": name, "ok": ok, "detail": detail, "hint": hint})

    add("In custom-items.csv", True, "the list this editor edits")
    tables = {t: server_table_keys(t) for t in ("ItemConfig", "WardRobeConfig", "TradeConfig")}
    if any(v is None for v in tables.values()):
        add("Server tables", False, "data\\config tables not found", "Check that the editor sits inside the V2 repository.")
    else:
        miss = []
        if str(item_id) not in tables["ItemConfig"]:
            miss.append("ItemConfig " + str(item_id))
        if str(ward_id) not in tables["WardRobeConfig"]:
            miss.append("WardRobeConfig " + str(ward_id))
        if truthy(row.get("Stall")) and str(item_id) not in tables["TradeConfig"]:
            miss.append("TradeConfig " + str(item_id))
        add("Server tables (data\\config)", not miss, "all rows present" if not miss else "missing: " + ", ".join(miss),
            "Press Save & Build, then restart the server.")
    tsv = ctx["tsv"]
    add("Client list (items.tsv)", key.lower() in tsv, "listed" if key.lower() in tsv else "not in items.tsv",
        "Press Save & Build.")
    hats = ctx["hats"]
    icons = ctx["icons"]
    models = ctx["models"]
    miss_model = []
    miss_txt = []
    for g in (x.upper() for x in cfg["genders"]):
        for ext in (".obj", ".png"):
            if (key + g + ext).lower() not in hats:
                miss_model.append(key + g + ext)
        if (key + g + ".txt").lower() not in hats:
            miss_txt.append(key + g + ".txt")
    add("Model + texture (ro3emu\\hats)", not miss_model, "present" if not miss_model else "missing: " + ", ".join(miss_model),
        "Import the item from the Origin files (button below).")
    add("Hat settings (.txt)", not miss_txt, "present" if not miss_txt else "missing: " + ", ".join(miss_txt),
        "Press Save & Build: it writes default settings once the model exists.")
    miss_icon = []
    for needed in ("item_%s.png" % item_id, "wardrobe_icon_%s_%s%s.png" % (cfg["prefix"], asset, cfg["icon_end"])):
        if needed.lower() not in icons:
            miss_icon.append(needed)
    add("Icons (custom\\icons)", not miss_icon, "present" if not miss_icon else "missing: " + ", ".join(miss_icon),
        "Import the item from the Origin files, or put the PNG files into customs\\assets\\icons and Deploy.")
    miss_alias = []
    for g in cfg["genders"]:
        for suf in ALIAS_SUFFIXES:
            n = "model_%s_%s%s_%s.alias" % (cfg["prefix"], asset, g, suf)
            if n.lower() not in models:
                miss_alias.append(n)
    add("Model aliases (custom\\models)", not miss_alias, "present" if not miss_alias else "%d missing" % len(miss_alias),
        "Press Save & Build.")
    return checks


def client_context():
    ro = client_dir() / "ro3emu"
    tsv = set()
    path = ro / "custom" / "items.tsv"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and line[0] != "#":
                tsv.add(line.split("\t", 1)[0].lower())
    except OSError:
        pass
    return {"tsv": tsv, "hats": names_in(ro / "hats"), "icons": names_in(ro / "custom" / "icons"),
            "models": names_in(ro / "custom" / "models")}


def item_json(row, ctx):
    slot = slot_of(row)
    cfg = B.SLOTS.get(slot, B.SLOTS["head"])
    checks = check_item(row, ctx)
    key = row["Key"]
    genders = [g.upper() for g in cfg["genders"]]
    out = {f: (row.get(f) or "") for f in FIELDS}
    out["Slot"] = slot
    out["Stall"] = truthy(row.get("Stall"))
    out["Rarity"] = rarity_name(row.get("Rarity")) or row.get("Rarity", "")
    out["genders"] = [g if g else "" for g in genders]
    out["live"] = {g: (key + g + ".obj").lower() in ctx["hats"] for g in genders}
    out["checks"] = checks
    out["ready"] = all(c["ok"] for c in checks)
    return out


# ---------------------------------------------------------------- hat settings files (the live tuner)

LIVE_NUM = {"scale": 1.0}


def hat_file(key, gender):
    return client_dir() / "ro3emu" / "hats" / (key + gender + ".txt")


def parse_opts(path):
    o = {"scale": 1.0, "rot": [0.0, 0.0, 0.0], "off": [0.0, 0.0, 0.0], "flipv": False, "mirrorx": False,
         "sway": 0, "swayamp": 10.0, "swayspeed": 2.2, "transparent": False, "extra": []}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return o

    def nums(text):
        out = []
        for part in text.split(","):
            try:
                out.append(float(part.strip()))
            except ValueError:
                out.append(0.0)
        return out

    for line in lines:
        eq = line.find("=")
        if eq <= 0:
            continue
        k, v = line[:eq].strip().lower(), line[eq + 1:].strip()
        n = nums(v)
        if k == "scale":
            o["scale"] = n[0]
        elif k == "rotate":
            o["rot"] = (n + [0, 0, 0])[:3]
        elif k == "offset":
            o["off"] = (n + [0, 0, 0])[:3]
        elif k == "flipv":
            o["flipv"] = n[0] > 0
        elif k == "mirrorx":
            o["mirrorx"] = n[0] > 0
        elif k == "sway":
            o["sway"] = int(n[0])
        elif k == "swayamp":
            o["swayamp"] = n[0]
        elif k == "swayspeed":
            o["swayspeed"] = n[0]
        elif k == "transparent":
            o["transparent"] = n[0] > 0
        else:
            o["extra"].append(line.strip())
    return o


def fmt(x, places):
    text = ("%." + str(places) + "f") % float(x)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def write_opts(path, o):
    """Same text format as the game's HatOptions.Save, so the game and this page write the same file."""
    lines = ["scale=" + fmt(o["scale"], 3),
             "rotate=" + ",".join(fmt(v, 1) for v in o["rot"]),
             "offset=" + ",".join(fmt(v, 3) for v in o["off"]),
             "flipv=" + ("1" if o["flipv"] else "0"),
             "mirrorx=" + ("1" if o["mirrorx"] else "0")]
    if int(o.get("sway", 0)) > 0:
        lines += ["sway=" + str(int(o["sway"])), "swayamp=" + fmt(o["swayamp"], 1), "swayspeed=" + fmt(o["swayspeed"], 2)]
    if o.get("transparent"):
        lines.append("transparent=1")
    lines += [x for x in o.get("extra", []) if x]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")


# ---------------------------------------------------------------- processes

def process_running(*names):
    if os.name != "nt":
        return False
    try:
        out = subprocess.check_output(["tasklist", "/FO", "CSV", "/NH"], text=True, stderr=subprocess.DEVNULL,
                                      creationflags=0x08000000)
    except Exception:
        return False
    low = out.lower()
    return any(n.lower() in low for n in names)


def server_running():
    return process_running("ro3emu.exe", "ro3emu.server.exe")


def game_running():
    return process_running('"ro3.exe"')


def run_build(check):
    cmd = [sys.executable, str(TOOLS / "build_custom_items.py"), "--client", Ctx.client]
    if check:
        cmd.append("--check")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=str(TOOLS), timeout=300, env=SUBPROCESS_ENV)
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "The build did not finish in 5 minutes and was stopped."}
    return {"ok": p.returncode == 0, "output": (p.stdout or "") + (p.stderr or "")}


def open_window(title, args, cwd=None):
    """Starts a command in its own console window that stays open (Windows)."""
    if os.name != "nt":
        raise RuntimeError("Starting windows is only supported on Windows.")
    subprocess.Popen(["cmd.exe", "/c", "start", title, "cmd", "/k"] + args, cwd=str(cwd or TOOLS))


def dev_bat():
    return REPO.parent / "RUN RO3 DEV SERVER.bat"


# ---------------------------------------------------------------- state / actions

def get_state():
    rows, _, _ = read_csv()
    ctx = client_context()
    items = [item_json(r, ctx) for r in rows]
    slots = {}
    for slot, cfg in B.SLOTS.items():
        item, ward = next_ids(rows, slot)
        slots[slot] = {"label": SLOT_LABEL[slot], "nextItem": item, "nextWard": ward,
                       "wardFirst": cfg["ward_first"], "wardLast": cfg["ward_last"], "genders": list(cfg["genders"])}
    return {"items": items, "slots": slots, "rarities": RARITIES, "client": Ctx.client,
            "itemFirst": B.ITEM_FIRST, "itemLast": B.ITEM_LAST, "csv": str(CSV_PATH),
            "server": server_running(), "game": game_running(), "bat": str(dev_bat()), "batFound": dev_bat().is_file()}


def save_item(body):
    with LOCK:
        rows, names, bom = read_csv()
        key = body.get("Key")
        row = next((r for r in rows if r["Key"] == key), None)
        if row is None:
            raise ValueError("That item is not in the list any more. Reload the page.")
        vals = clean_fields(dict(body, Slot=slot_of(row)), rows, current_key=key)
        vals.pop("Slot")
        row.update(vals)
        write_csv(rows, names, bom)
    return get_state()


def add_item(body):
    with LOCK:
        rows, names, bom = read_csv()
        slot = (body.get("Slot") or "head").strip().lower()
        if slot not in B.SLOTS:
            raise ValueError("Pick a slot.")
        name = (body.get("ItemName") or "").strip()
        if not name:
            raise ValueError("Type the item name.")
        item, ward = next_ids(rows, slot)
        data = dict(body, Slot=slot)
        if not str(data.get("ItemID") or "").strip():
            data["ItemID"] = item
        if not str(data.get("WardrobeID") or "").strip():
            data["WardrobeID"] = ward
        if not str(data.get("StyleBonus") or "").strip():
            data["StyleBonus"] = 10
        if not data.get("Rarity"):
            data["Rarity"] = "Advanced"
        if "Stall" not in data:
            data["Stall"] = True
        if truthy(data.get("Stall")) and not str(data.get("StallPrice") or "").strip():
            data["StallPrice"] = 25000
        vals = clean_fields(data, rows)
        key = (body.get("Key") or "").strip() or camel(name, {r["Key"].lower() for r in rows})
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,40}", key):
            raise ValueError("The key must be letters and digits only, no spaces.")
        if key.lower() in {r["Key"].lower() for r in rows}:
            raise ValueError(f"The key {key} is already used.")
        new = {f: "" for f in names}
        new.update(vals)
        new["Key"] = key
        if slot == "head":
            new["Slot"] = "head"
        rows.append(new)
        write_csv(rows, names, bom)
    state = get_state()
    state["added"] = key
    return state


def delete_item(body):
    with LOCK:
        rows, names, bom = read_csv()
        left = [r for r in rows if r["Key"] != body.get("Key")]
        if len(left) == len(rows):
            raise ValueError("That item is not in the list.")
        write_csv(left, names, bom)
    return get_state()


def origin_search(query, slot):
    cmd = [sys.executable, str(TOOLS / "import_hat.py"), "--list", query, "--client", Ctx.client]
    if slot and slot != "head":
        cmd += ["--slot", slot]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(TOOLS), timeout=120, env=SUBPROCESS_ENV)
    out = []
    for line in (p.stdout or "").splitlines():
        if re.match(r"^\d+ hat\(s\)$", line.strip()) or not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) >= 2:
            out.append({"english": parts[0], "internal": parts[1]})
    err = "" if p.returncode == 0 else ((p.stdout or "") + (p.stderr or "")).strip()[-400:]
    return {"results": out[:60], "error": err}


def start_import(body):
    origin = (body.get("origin") or "").strip()
    slot = (body.get("slot") or "head").strip()
    if not origin:
        raise ValueError("This item has no Origin name. Type or search one first.")
    if slot not in B.SLOTS:
        raise ValueError("Unknown slot.")
    open_window("Import from Origin", [sys.executable, str(TOOLS / "import_hat.py"), origin, "--slot", slot,
                                       "--client", Ctx.client])
    return {"ok": True}


def run_game(body):
    if server_running():
        raise ValueError("A Ro3Emu server is already running. Close it first (two servers cannot share the ports); "
                         "if you built, it also needs a restart to load the new tables.")
    bat = dev_bat()
    if not bat.is_file():
        raise ValueError(f"Cannot find {bat}.")
    result = {"build": None}
    if body.get("build", True):
        result["build"] = run_build(False)
        if not result["build"]["ok"]:
            raise ValueError("The build failed, so nothing was started:\n" + result["build"]["output"][-1500:])
    args = ["rebuild"] if body.get("rebuild") else []
    if os.name != "nt":
        raise RuntimeError("Starting the game is only supported on Windows.")
    subprocess.Popen(["cmd.exe", "/c", "start", "RO3 dev server", str(bat)] + args, cwd=str(bat.parent))
    result["ok"] = True
    return result


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
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
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                return self.send(200, (STATIC / "index.html").read_bytes(), "text/html")
            if url.path == "/favicon.ico":
                return self.send(204, b"", "image/x-icon")
            if url.path == "/api/state":
                return self.ok(get_state())
            if url.path == "/api/status":
                return self.ok({"server": server_running(), "game": game_running()})
            if url.path == "/api/hat/get":
                key, g = q.get("key", ""), q.get("g", "")
                if not re.fullmatch(r"[A-Za-z0-9]+", key) or g not in ("", "M", "F"):
                    raise ValueError("bad key")
                path = hat_file(key, g)
                o = parse_opts(path)
                o["exists"] = path.is_file()
                o["path"] = str(path)
                return self.ok(o)
            if url.path == "/api/origin/search":
                return self.ok(origin_search(q.get("q", ""), q.get("slot", "head")))
            self.fail(404, "Not found")
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            self.fail(400, exc)
        except Exception as exc:  # noqa: BLE001
            self.fail(500, exc)

    def do_POST(self):
        if not self.local_only():
            return self.fail(403, "Forbidden")
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
            if path == "/api/item/save":
                return self.ok(save_item(body))
            if path == "/api/item/add":
                return self.ok(add_item(body))
            if path == "/api/item/delete":
                return self.ok(delete_item(body))
            if path == "/api/build":
                return self.ok(run_build(bool(body.get("check"))))
            if path == "/api/import":
                return self.ok(start_import(body))
            if path == "/api/run":
                return self.ok(run_game(body))
            if path == "/api/client":
                set_client(body.get("dir", ""))
                return self.ok(get_state())
            if path == "/api/hat/set":
                key, g = body.get("key", ""), body.get("g", "")
                if not re.fullmatch(r"[A-Za-z0-9]+", key) or g not in ("", "M", "F"):
                    raise ValueError("bad key")
                o = body.get("opts") or {}
                o.setdefault("extra", parse_opts(hat_file(key, g))["extra"])
                write_opts(hat_file(key, g), o)
                if body.get("both") and g in ("M", "F"):
                    other = "F" if g == "M" else "M"
                    if (client_dir() / "ro3emu" / "hats" / (key + other + ".obj")).is_file():
                        write_opts(hat_file(key, other), o)
                return self.ok({"saved": datetime.now().strftime("%H:%M:%S")})
            self.fail(404, "Not found")
        except (ValueError, OSError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
            self.fail(400, exc)
        except Exception as exc:  # noqa: BLE001
            self.fail(500, exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", help="game folder (default: the saved one, else " + DEFAULT_CLIENT + ")")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    Ctx.client = args.client or read_settings().get("client") or DEFAULT_CLIENT
    if not (Path(Ctx.client) / "ro3emu").is_dir():
        print(f"Warning: {Ctx.client} has no ro3emu folder yet; set the game folder on the page.")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"RO3 Eclipse Custom Items Editor\n  list : {CSV_PATH}\n  game : {Ctx.client}\n  open : {url}\n  (close this window to stop)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
