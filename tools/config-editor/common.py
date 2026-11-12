"""Small helpers shared by the skill editor (app.py) and the item editor (items.py). Standard library only."""
import hashlib
import json
import os
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
