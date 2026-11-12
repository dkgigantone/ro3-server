"""Small helpers shared by the skill editor (app.py) and the item editor (items.py). Standard library only."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
STATE_DIR = APP_DIR / "state"
LOCK = threading.RLock()
_SHA_CACHE = {}


def sha256(path: Path) -> str:
    """SHA-256 of a file, remembered until the file's size or modified time changes (configs are several MB)."""
    stt = path.stat()
    key = (str(path), stt.st_mtime_ns, stt.st_size)
    if key in _SHA_CACHE:
        return _SHA_CACHE[key]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if len(_SHA_CACHE) > 16:
        _SHA_CACHE.clear()
    _SHA_CACHE[key] = h.hexdigest()
    return _SHA_CACHE[key]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        json.loads(Path(tmp).read_text(encoding="utf-8"))  # must parse before it replaces anything
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


OVERLAY_HEADER = "# RO3 Plus Config Editor: edited rows the client should show. Table<TAB>Row<TAB>Field<TAB>value"
CLIENT_SETTINGS = STATE_DIR / "client.json"
LAST_PUBLISH = {"ok": False, "message": "not published yet"}


def client_dir():
    """The game client folder (the one with ro3_Data): saved choice, RO3_CLIENT_DIR, or the usual place. None if unknown."""
    try:
        saved = json.loads(CLIENT_SETTINGS.read_text(encoding="utf-8")).get("dir")
    except (OSError, ValueError):
        saved = None
    for cand in (saved, os.environ.get("RO3_CLIENT_DIR"), r"C:\Games\Ragnarok3-Official"):
        if cand and (Path(cand) / "ro3_Data").is_dir():
            return Path(cand)
    return None


def set_client_dir(path):
    p = Path(str(path).strip().strip('"'))
    if not (p / "ro3_Data").is_dir():
        raise ValueError(f"{p} is not a game client folder (it has no ro3_Data folder inside).")
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_SETTINGS.write_text(json.dumps({"dir": str(p)}), encoding="utf-8")
    return p


def overlay_path():
    return APP_DIR / "client-overlay" / "config-overlay.tsv"


def publish_overlay():
    """Copy the overlay into <client>\\ro3emu\\custom\\ (the previous one is kept as config-overlay.tsv.bak)."""
    global LAST_PUBLISH
    client = client_dir()
    if client is None:
        LAST_PUBLISH = {"ok": False, "message": "No client folder found. Set it on the Shops page (Client folder)."}
        return LAST_PUBLISH
    dest = client / "ro3emu" / "custom" / "config-overlay.tsv"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.copy2(dest, dest.with_suffix(".tsv.bak"))
        shutil.copy2(overlay_path(), dest)
        LAST_PUBLISH = {"ok": True, "message": str(dest)}
    except OSError as e:
        LAST_PUBLISH = {"ok": False, "message": f"Could not copy the overlay to {dest}: {e}"}
    return LAST_PUBLISH


def update_overlay(tables, new_lines):
    """client-overlay/config-overlay.tsv is shared by the Items and Shops pages. Replace only the lines of `tables`
    (this page's own), keep every other page's lines, then copy the file into the client. With no overlay yet, the
    client's own file is the starting point so edits made earlier are not lost."""
    path = overlay_path()
    source = path
    if not path.exists():
        client = client_dir()
        theirs = client / "ro3emu" / "custom" / "config-overlay.tsv" if client else None
        if theirs is not None and theirs.exists():
            source = theirs
    keep = []
    if source.exists():
        for line in source.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and line.split("\t", 1)[0] not in tables:
                keep.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join([OVERLAY_HEADER] + keep + list(new_lines)) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    publish_overlay()
    return str(path)


def compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def server_running() -> bool:
    if os.name != "nt":
        return False
    try:
        out = subprocess.check_output(["tasklist", "/FO", "CSV", "/NH"], text=True,
                                      stderr=subprocess.DEVNULL, creationflags=0x08000000)
    except Exception:
        return False
    low = out.lower()
    return "ro3emu.exe" in low or "ro3emu.server.exe" in low
