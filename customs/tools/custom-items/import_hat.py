#!/usr/bin/env python3
"""Imports Origin hats into Ro3Emu as custom items.

Just double-click Import-Hat.cmd and answer the questions:
  1. type the English name (or a part of it)      2. pick a number from the results
  3. look at the icon picture it shows            4. answer y (yes) or n (no)
If yes, the hat is extracted, put into the game folder and added to custom-items.csv, and the custom items are rebuilt.
Close the game and the server first. Tune size and position afterwards with HatStudio.

Power users can still pass names:  python import_hat.py "Cat-Ear Beret"   or search only:  --list cat
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_custom_items import SLOTS as ITEM_SLOTS  # noqa: E402  (the same slot table the build uses)
NAMES_CSV = HERE / "origin-headgear-english.csv"
ITEMS_CSV = HERE / "custom-items.csv"
DEFAULT_TXT = "scale=%s\r\nrotate=-82.34,0.2,0\r\noffset=0,%s,-0.25\r\nflipv=0\r\nmirrorx=0\r\n"


def die(message):
    print("\nSTOPPED: " + message)
    sys.exit(1)


MY_NAMES_CSV = HERE / "my-hat-names.csv"


def load_names():
    """The English list, with your own corrections from my-hat-names.csv (internal_name,english_name) on top."""
    if not NAMES_CSV.is_file():
        die("cannot find " + str(NAMES_CSV))
    with open(NAMES_CSV, encoding="utf-8-sig", newline="") as f:
        rows = [[r["internal_name"].strip(), r["english_name"].strip(), r.get("confidence", "").strip()] for r in csv.DictReader(f)]
    if MY_NAMES_CSV.is_file():
        mine = {}
        with open(MY_NAMES_CSV, encoding="utf-8-sig", newline="") as f:
            for line in csv.reader(f):
                if len(line) >= 2 and line[0].strip() and line[0].strip().lower() != "internal_name":
                    mine[line[0].strip().lower()] = line[1].strip()
        for r in rows:
            if r[0].lower() in mine:
                r[1], r[2] = mine.pop(r[0].lower()), "mine"
        for internal, english in mine.items():
            rows.append([internal, english, "mine"])
    return [tuple(r) for r in rows]


def norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def search(query, names):
    """Every word typed must be the start of a word in the English name (or appear in the Origin name)."""
    words = [norm(w) for w in query.split() if norm(w)]
    out = []
    for n in names:
        eng = [w.lower() for w in re.findall(r"[A-Za-z0-9]+", n[1])]
        if words and all(any(e.startswith(w) for e in eng) or (len(w) >= 4 and w in n[0].lower()) for w in words):
            out.append(n)
    return out


def resolve(arg, names):
    """-> (internal, english) or None (after printing why)."""
    exact = [n for n in names if n[0].lower() == arg.lower() or n[1].lower() == arg.lower()]
    if len(exact) == 1:
        return exact[0][0], exact[0][1]
    found = search(arg, names)
    if len(found) == 1:
        return found[0][0], found[0][1]
    if not found:
        print("  '%s': no hat with that name. Try fewer words, or --list <word>." % arg)
    else:
        print("  '%s' matches %d hats - use the exact name:" % (arg, len(found)))
        for n in found[:25]:
            print("      %-32s %s" % (n[1], n[0]))
        if len(found) > 25:
            print("      ... and %d more" % (len(found) - 25))
    return None


def camel(english, taken):
    words = re.findall(r"[A-Za-z0-9]+", english)
    key = "".join(w[:1].upper() + w[1:] for w in words) or "Hat"
    if not key[0].isalpha():
        key = "Hat" + key
    key = key[:40]
    base, n = key, 2
    while key.lower() in taken:
        key = base + str(n)
        n += 1
    return key


HEAD_DIRS = ("icon_wear_head_01", "icon_wear_head_02", "icon_itemheadwear01", "icon_itemheadwear02")
OTHER_DIRS = ("icon_wear_other_01", "icon_wear_other_02")
# slot -> (Origin file prefix, tab label, icon folders). Hats use "helmet"; Facewear is called "adornment" in the files.
SLOTS = {
    "helmet": ("Headwear", HEAD_DIRS),
    "adornment": ("Facewear", OTHER_DIRS),
    "mouth": ("Mouthwear", OTHER_DIRS),
    "back": ("Backwear", OTHER_DIRS),
}


# custom-items.csv Slot value -> (Origin slot name, label, what to call it)
SLOT_MAP = {"head": "helmet", "face": "adornment", "mouth": "mouth", "back": "back"}
SLOT_LABEL = {"head": "Headwear", "face": "Facewear", "mouth": "Mouthwear", "back": "Backwear"}


def row_slot(row):
    return (row.get("Slot") or "head").strip().lower() or "head"


def load_my_names():
    """internal name (lower case) -> the name you gave it in my-hat-names.csv."""
    mine = {}
    if MY_NAMES_CSV.is_file():
        with open(MY_NAMES_CSV, encoding="utf-8-sig", newline="") as f:
            for line in csv.reader(f):
                if len(line) >= 2 and line[0].strip() and line[0].strip().lower() != "internal_name":
                    mine[line[0].strip().lower()] = line[1].strip()
    return mine


def slot_names(idx):
    """Facewear/Mouthwear/Backwear have no English list: the Origin names, plus your own names from my-hat-names.csv."""
    mine = load_my_names()
    out = []
    models = idx.all_models()
    for internal in sorted(models):
        if internal.endswith("_extension") and internal[:-len("_extension")] in models:
            continue  # a part of the main item; it is imported together with it
        english = mine.get(internal) or internal.replace("_", " ").title()
        out.append((internal, english, "mine" if internal in mine else ""))
    return out


class Index:
    """One pass over roo-index2.csv: where every hat model and every item icon lives."""

    def __init__(self, path, slot="helmet"):
        self.slot = slot
        self.icon_dirs = SLOTS[slot][1]
        self.models = {}   # origin name -> (block, offset, size)
        self.asset_models = {}  # newer hats: the mesh sits in equipments/mount/helmet_<name>_01.asset instead of a .fbx
        self.tex_rows = {}  # origin name -> rows that hold the hat's .tga (sometimes a different bundle than the .fbx)
        self.icons = {}    # icon name -> [(priority, (block, offset, size))]
        mpat = re.compile(r"/handles/%s/%s_([a-z0-9_]+)\.fbx" % (slot, slot), re.I)
        tpat = re.compile(r"/handles/%s/%s_([a-z0-9_]+)\.tga" % (slot, slot), re.I)
        apat = re.compile(r"/equipments/mount/%s_([a-z0-9_]+?)_01\.asset" % slot, re.I)
        m_key = "/handles/%s/%s_" % (slot, slot)
        a_key = "/equipments/mount/%s_" % slot
        ipat = re.compile(r"/([a-z0-9_]+)/ui_icon_item_([a-z0-9_]+)\.png", re.I)
        with open(path, encoding="utf-8", newline="") as f:
            for line in f:
                low = line.lower()
                has_m = m_key in low
                has_a = a_key in low and ".asset" in low
                has_i = "/ui_icon_item_" in low
                if not (has_m or has_a or has_i):
                    continue
                block, offset, size, paths = line.rstrip("\r\n").split(",", 3)
                row = (block, int(offset), int(size))
                if has_m:
                    for m in mpat.finditer(paths):
                        self.models[m.group(1).lower()] = row
                    for m in tpat.finditer(paths):
                        self.tex_rows.setdefault(m.group(1).lower(), set()).add(row)
                if has_a:
                    for m in apat.finditer(paths):
                        self.asset_models[m.group(1).lower()] = row
                if has_i:
                    for m in ipat.finditer(paths):
                        d = m.group(1).lower()
                        prio = self.icon_dirs.index(d) if d in self.icon_dirs else 9
                        self.icons.setdefault(m.group(2).lower(), []).append((prio, row))
        self.head_icons = [n for n, v in self.icons.items() if any(p < 9 for p, _ in v)]

    def all_models(self):
        """Every hat name that has a mesh: the .fbx kind and the newer .asset kind."""
        return set(self.models) | set(self.asset_models)

    def model(self, internal):
        low = internal.lower()
        return self.models.get(low) or self.asset_models.get(low)

    def parts(self, internal):
        """Every bundle row that holds a piece of the hat model (mesh first, then texture bundles)."""
        low = internal.lower()
        first = self.model(low)
        rows = [first] if first else []
        # An "<name>_extension" part (tinted glass of goggles, ...) is a second mesh of the same item.
        ext = self.model(low + "_extension")
        if ext and ext not in rows:
            rows.append(ext)
        # The texture has the item's name; a part such as "<name>_extension" shares the main item's texture.
        name = low
        while name and name not in self.tex_rows:
            name = name.rpartition("_")[0]
        rows += sorted(r for r in self.tex_rows.get(name, ()) if r not in rows)
        return rows

    def icon(self, internal):
        """-> (row, name, exact) or (None, None, False). Exact name first, then a similar headgear icon name."""
        low = internal.lower()
        slot = self.slot
        for cand in (low, slot + "_" + low, slot + low):
            if cand in self.icons:
                return min(self.icons[cand])[1], cand, True
        best = None
        for n in self.head_icons:
            core = n[len(slot):].lstrip("_") if n.startswith(slot) else n
            if core.startswith(low) or (len(core) >= 5 and low.startswith(core)):
                gap = abs(len(n) - len(low))
                if best is None or gap < best[0]:
                    best = (gap, n)
        if best:
            return min((p, r) for p, r in self.icons[best[1]] if p < 9)[1], best[1], False
        # Last resort: the icon name has extra letters in the middle (bingbaomianju -> bingbaoqishimianju).
        def inside(short, long_):
            it = iter(long_)
            return all(ch in it for ch in short)
        for n in self.head_icons:
            core = n[len(slot):].lstrip("_") if n.startswith(slot) else n
            for a, b in ((low, core), (core, low)):
                if len(a) >= 8 and len(b) - len(a) <= 8 and inside(a, b):
                    gap = abs(len(core) - len(low))
                    if best is None or gap < best[0]:
                        best = (gap, n)
        if best:
            return min((p, r) for p, r in self.icons[best[1]] if p < 9)[1], best[1], False
        return None, None, False


def anim_rows(index_path, internal, slot="helmet"):
    """Every bundle row that mentions this item's files of the slot (mesh, texture, material, controller, clips, prefabs)."""
    pat = re.compile(r"%s_%s(?=[._])" % (slot, re.escape(internal.lower())))
    rows = []
    with open(index_path, encoding="utf-8", newline="") as f:
        for line in f:
            if slot + "_" + internal.lower() not in line.lower():
                continue
            block, offset, size, paths = line.rstrip("\r\n").split(",", 3)
            tokens = [p.strip().lower() for p in paths.split(" | ")]
            if any(pat.search(t) and "/ui/" not in t for t in tokens):
                row = (block, int(offset), int(size))
                if row not in rows:
                    rows.append(row)
    return rows


def export_anim(cfg, internal, key):
    """Copies the raw Origin bundles of the hat to <client>/ro3emu/hat-anim/<Key>/ for the animated-hat route."""
    folder = cfg.client / "ro3emu" / "hat-anim" / key
    folder.mkdir(parents=True, exist_ok=True)
    rows = anim_rows(cfg.index, internal)
    ok = 0
    for n, (block, offset, size) in enumerate(rows, start=1):
        src = Path(cfg.origin) / block
        with open(src, "rb") as f:
            f.seek(offset)
            data = f.read(size)
        if len(data) == size and data.startswith(b"UnityFS"):
            (folder / ("%02d_%s.bundle" % (n, Path(block).stem))).write_bytes(data)
            ok += 1
        else:
            print("  skipped one file (not a Unity bundle): %s offset %d" % (block, offset))
    print("  animation files: %d bundles written to %s" % (ok, folder))
    return ok


def inspect(cfg, internal, key):
    """Exports everything AssetStudio can read from the hat's bundles (and an FBX with skeleton and animation) for analysis."""
    root = cfg.client / "ro3emu" / "hat-anim" / key / "inspect"
    shutil.rmtree(root, ignore_errors=True)
    rows = anim_rows(cfg.index, internal)
    for n, row in enumerate(rows, start=1):
        d = root / ("%02d_%s" % (n, Path(row[0]).stem))
        carve(cfg.origin, row, d / "x.bundle")
        for label, kinds in (("all", "all"), ("fbx", "animator")):
            r = subprocess.run([cfg.assetstudio, str(d / "x.bundle"), "-o", str(d / label), "-t", kinds, "-g", "type"], capture_output=True, text=True)
            if r.returncode != 0 and label == "all":
                subprocess.run([cfg.assetstudio, str(d / "x.bundle"), "-o", str(d / label)], capture_output=True, text=True)
        (d / "x.bundle").unlink()
    lines = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            lines.append("%8d  %s" % (p.stat().st_size, p.relative_to(root)))
    (root / "inspect.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("  %d files exported to %s" % (len(lines), root))
    print("  list: " + str(root / "inspect.txt"))


def peek(cfg, internal, slot="helmet"):
    """Read-only look at ANY Origin hat (also ones that cannot be imported yet): exports everything AssetStudio can read."""
    root = cfg.client / "ro3emu" / "hat-peek" / internal
    shutil.rmtree(root, ignore_errors=True)
    rows = anim_rows(cfg.index, internal, slot)
    if not rows:
        die("no %s files found in the Origin index for %s" % (slot, internal))
    for n, row in enumerate(rows, start=1):
        d = root / ("%02d_%s" % (n, Path(row[0]).stem))
        carve(cfg.origin, row, d / "x.bundle")
        r = subprocess.run([cfg.assetstudio, str(d / "x.bundle"), "-o", str(d / "all"), "-t", "all", "-g", "type"], capture_output=True, text=True)
        if r.returncode != 0:
            subprocess.run([cfg.assetstudio, str(d / "x.bundle"), "-o", str(d / "all")], capture_output=True, text=True)
        (d / "x.bundle").unlink()
    lines = ["%8d  %s" % (p.stat().st_size, p.relative_to(root)) for p in sorted(root.rglob("*")) if p.is_file()]
    (root / "peek.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("  %d bundles read, %d files exported to %s" % (len(rows), len(lines), root))
    print("  list: " + str(root / "peek.txt"))


def carve(block_dir, row, out):
    block, offset, size = row
    src = Path(block_dir) / block
    if not src.is_file():
        die("Origin file not found: " + str(src))
    with open(src, "rb") as f:
        f.seek(offset)
        data = f.read(size)
    if len(data) != size or not data.startswith(b"UnityFS"):
        die("no valid bundle at %s offset %d" % (block, offset))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


def export(assetstudio, bundle, folder, kinds):
    folder.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([assetstudio, str(bundle), "-o", str(folder), "-t", kinds, "-g", "type"], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-600:], r.stderr[-600:])
        die("AssetStudio failed on " + bundle.name)


def biggest(folder, pattern):
    files = sorted(folder.rglob(pattern), key=lambda p: p.stat().st_size, reverse=True)
    return files[0] if files else None


def mesh_defaults(obj_path):
    xs, zs = [], []
    with open(obj_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("v "):
                a = line.split()
                xs.append(float(a[1]))
                zs.append(float(a[3]))
    if not xs:
        return 1.2, -2.8
    width = max(xs) - min(xs)
    scale = round(min(3.0, max(0.3, 0.9 / width)), 2) if width > 0 else 1.2
    centre = (max(zs) + min(zs)) / 2
    return scale, round(-(centre * scale) - 0.05, 2)


def shrink_icon(src, dst):
    try:
        from PIL import Image
    except ImportError:
        shutil.copyfile(src, dst)
        return False
    img = Image.open(src).convert("RGBA")
    w, h = img.size
    small = img.resize((max(1, int(w * 0.72)), max(1, int(h * 0.72))), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(small, ((w - small.width) // 2, (h - small.height) // 2), small)
    canvas.save(dst)
    return True


def read_items():
    with open(ITEMS_CSV, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    fields = list(reader.fieldnames or [])
    if "Slot" not in fields:
        fields.append("Slot")
    return list(reader), fields, raw.startswith(b"\xef\xbb\xbf")


def next_ids(rows, slot="head"):
    first, last = ITEM_SLOTS[slot]["ward_first"], ITEM_SLOTS[slot]["ward_last"]
    items = [int(r["ItemID"]) for r in rows if (r.get("ItemID") or "").strip()]
    wards = [int(r["WardrobeID"]) for r in rows if (r.get("WardrobeID") or "").strip() and first <= int(r["WardrobeID"]) <= last]
    item = max(items) + 1 if items else 3004901
    ward = max(wards) + 1 if wards else first
    if item > 3004999 or ward > last:
        die("the custom ID range is full (items 30049xx / %s looks %d-%d)" % (SLOT_LABEL[slot], first, last))
    return item, ward


class Setup:
    pass


def save_rows(cfg):
    try:
        f = open(ITEMS_CSV, "w", newline="", encoding="utf-8-sig" if cfg.bom else "utf-8")
    except PermissionError:
        die("cannot write custom-items.csv - it is probably open in Excel or another program. Close it and run the import again "
            "(the files already copied are fine; nothing is lost).")
    with f:
        w = csv.DictWriter(f, fieldnames=cfg.fields, lineterminator="\r\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(cfg.rows)


def extract_icon(cfg, icon_row, work):
    """Extracts an inventory icon; returns the png path or None."""
    if not icon_row:
        return None
    carve(cfg.origin, icon_row, work / "icon" / "i.bundle")
    export(cfg.assetstudio, work / "icon" / "i.bundle", work / "icon-out", "tex2d")
    return biggest(work / "icon-out", "*.png")


def merge_objs(files, target):
    """Several OBJ meshes (a hat body plus a feather, ...) become one OBJ; every mesh keeps an 'o <name>' marker."""
    v_off = vt_off = 0
    out = []
    for f in files:
        v = vt = 0
        out.append("o " + f.stem)
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("v "):
                    v += 1
                    out.append(line.rstrip())
                elif line.startswith("vt "):
                    vt += 1
                    out.append(line.rstrip())
                elif line.startswith("f "):
                    parts = []
                    for tok in line.split()[1:]:
                        idx = tok.split("/")
                        a = str(int(idx[0]) + v_off)
                        if len(idx) > 1 and idx[1]:
                            a += "/" + str(int(idx[1]) + vt_off)
                            if len(idx) > 2 and idx[2]:
                                a += "/" + str(int(idx[2]) + v_off)
                        elif len(idx) > 2:
                            a += "//" + str(int(idx[2]) + v_off)
                        parts.append(a)
                    out.append("f " + " ".join(parts))
        v_off += v
        vt_off += vt
    target.write_text("\n".join(out) + "\n", encoding="utf-8")


def export_model(cfg, rows, work):
    """Extracts the hat model from all its bundles; returns (obj, texture) or (None, None). All meshes are merged."""
    for n, row in enumerate(rows):
        carve(cfg.origin, row, work / ("model%d" % n) / "m.bundle")
        export(cfg.assetstudio, work / ("model%d" % n) / "m.bundle", work / ("model-out%d" % n), "tex2d,mesh")
    objs = sorted(work.rglob("*.obj"), key=lambda p: p.stat().st_size, reverse=True)
    cfg.multi = len(objs) > 1
    tex = biggest(work, "*.png")
    if not objs:
        return None, tex
    if len(objs) == 1:
        return objs[0], tex
    merged = work / "merged.obj"
    merge_objs(objs, merged)
    print("  note: this hat has %d separate meshes (%s) - all are included" % (len(objs), ", ".join(p.stem for p in objs)))
    return merged, tex


def has_see_through(tex):
    """True when more than 1% of the texture's pixels are not fully opaque (tinted glass)."""
    try:
        from PIL import Image
        alpha = Image.open(tex).convert("RGBA").getchannel("A")
        hist = alpha.histogram()
        return sum(hist[:250]) > 0.01 * sum(hist)
    except Exception:  # noqa: BLE001
        return False


def placeholder_icon(tex, dst):
    """No real icon found: a 128x128 picture made from the model texture (needs Pillow)."""
    try:
        from PIL import Image
        Image.open(tex).convert("RGBA").resize((128, 128)).save(dst)
        return True
    except Exception:  # noqa: BLE001
        return False


def import_one(cfg, internal, english, icon_png, obj, tex, existing):
    slot = cfg.slot
    sc = ITEM_SLOTS[slot]
    """Puts everything in place. existing = the CSV row when the hat is already in the list."""
    if existing:
        key, item_id, ward_id = existing["Key"], int(existing["ItemID"]), int(existing["WardrobeID"])
    else:
        key = camel(english, cfg.taken)
        cfg.taken.add(key.lower())
        item_id, ward_id = next_ids(cfg.rows, slot)
    scale, off_y = mesh_defaults(obj)
    hats_dir = cfg.client / "ro3emu" / "hats"
    icons_dir = cfg.client / "ro3emu" / "custom" / "icons"
    hats_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)
    for g in (x.upper() for x in sc["genders"]):
        shutil.copyfile(obj, hats_dir / (key + g + ".obj"))
        shutil.copyfile(tex, hats_dir / (key + g + ".png"))
        txt = hats_dir / (key + g + ".txt")  # your tuned size is never overwritten
        glass = getattr(cfg, "multi", False) and has_see_through(tex)
        if not txt.exists():
            txt.write_bytes((DEFAULT_TXT % (scale, off_y) + ("transparent=1\r\n" if glass else "")).encode("utf-8"))
        elif glass and "transparent" not in txt.read_text(encoding="utf-8", errors="replace").lower():
            with open(txt, "ab") as f:  # only this one line is added to your settings
                f.write(b"transparent=1\r\n")
    item_icon = icons_dir / ("item_%d.png" % item_id)
    ward_icon = icons_dir / ("wardrobe_icon_%s_%s%s.png" % (sc["prefix"], key.lower(), sc["icon_end"]))
    if icon_png:
        shutil.copyfile(icon_png, item_icon)
        if not shrink_icon(icon_png, ward_icon):
            print("  note: Pillow is not installed, so the wardrobe icon is full size (may look too big). Fix: python -m pip install pillow")
    elif placeholder_icon(tex, item_icon):
        shutil.copyfile(item_icon, ward_icon)
        shrink_icon(item_icon, ward_icon)
        print("  note: no inventory icon was found, so the icons are made from the model texture (you can replace item_%d.png later)" % item_id)
    else:
        print("  note: no inventory icon was found - the bag will show no picture until you add item_%d.png" % item_id)
    if not existing:
        row = {f: "" for f in cfg.fields}
        row.update({"Key": key, "ItemName": english, "Description": "%s, imported from Origin." % english, "StyleBonus": "10",
                    "Rarity": "Advanced", "Stall": "True", "StallPrice": "25000", "OriginHat": internal,
                    "ItemID": str(item_id), "WardrobeID": str(ward_id), "Slot": slot})
        cfg.rows.append(row)
        save_rows(cfg)
    if getattr(cfg, "multi", False) and has_see_through(tex):
        print("  note: this item has several parts and see-through glass; the settings get transparent=1")
    print("  model file: %s" % (hats_dir / (key + sc["genders"][0].upper() + ".obj")))
    print("  icons     : %s" % item_icon)
    print("  list file : %s  (%s)" % (ITEMS_CSV, "row kept, files refreshed" if existing else "new row added"))
    print("  %s = item %d, wardrobe %d, start size %s" % (key, item_id, ward_id, scale))
    return key


def carve_all(cfg, rows, folder, label):
    """Writes every distinct bundle row to folder/<index>.bundle. Returns {row: file}."""
    folder.mkdir(parents=True, exist_ok=True)
    done, bad = {}, 0
    for n, row in enumerate(rows, start=1):
        target = folder / ("b%04d.bundle" % n)
        if not target.exists():
            try:
                with open(Path(cfg.origin) / row[0], "rb") as f:
                    f.seek(row[1])
                    data = f.read(row[2])
                if len(data) != row[2] or not data.startswith(b"UnityFS"):
                    raise OSError("not a bundle")
                target.write_bytes(data)
            except OSError:
                bad += 1
                continue
        done[row] = target
    print("  %s: %d bundles read, %d skipped" % (label, len(done), bad))
    return done


def export_folder(cfg, bundles, work):
    """AssetStudio over a whole folder of bundles; falls back to one by one."""
    if work.exists() and any(work.rglob("*.png")):
        return
    try:
        export(cfg.assetstudio, bundles, work, "tex2d")
    except SystemExit:
        pass
    if not any(work.rglob("*.png")):
        print("  (reading the folder at once did not work; doing it one by one - slower)")
        for b in sorted(bundles.glob("*.bundle")):
            export(cfg.assetstudio, b, work / b.stem, "tex2d")


def gallery(cfg, names, idx):
    """Writes gallery/hats.html: every item of the four slots (Headwear, Facewear, Mouthwear, Backwear) with a picture,
    its name, tabs per slot and a search box."""
    import html
    out = HERE / "gallery"
    img = out / "img"
    for stale in ("bundles-icons", "out-icons", "bundles-models", "out-models"):
        shutil.rmtree(out / stale, ignore_errors=True)  # cached bundle numbers change when the lists do
    img.mkdir(parents=True, exist_ok=True)
    indexes = {"helmet": idx}
    for slot in SLOTS:
        if slot not in indexes:
            print("Reading the file index for %s ..." % SLOTS[slot][0])
            indexes[slot] = Index(cfg.index, slot)
    wanted = {}   # (slot, name) -> (icon row or None, icon name, exact)
    for slot, ix in indexes.items():
        for internal in sorted(ix.all_models()):
            wanted[(slot, internal)] = ix.icon(internal)
    icon_rows = sorted({v[0] for v in wanted.values() if v[0]})
    print("Extracting %d icons and the model pictures of items without an icon (a few minutes the first time) ..." % len(icon_rows))
    carve_all(cfg, icon_rows, out / "bundles-icons", "icons")
    export_folder(cfg, out / "bundles-icons", out / "out-icons")
    pngs = {p.stem.lower()[len("ui_icon_item_"):]: p for p in (out / "out-icons").rglob("*.png") if p.stem.lower().startswith("ui_icon_item_")}
    kind = {}
    for (slot, internal), (row, name, exact) in wanted.items():
        if name and name in pngs:
            shutil.copyfile(pngs[name], img / ("%s-%s.png" % (slot, internal)))
            kind[(slot, internal)] = "" if exact else "similar icon"
    missing = [k for k in wanted if k not in kind]
    if missing:
        rows = sorted({r for slot, m in missing for r in indexes[slot].parts(m)})
        carve_all(cfg, rows, out / "bundles-models", "model textures")
        export_folder(cfg, out / "bundles-models", out / "out-models")
        tex = {}
        for p in (out / "out-models").rglob("*.png"):
            st = p.stem.lower()
            for slot in SLOTS:
                if st.startswith(slot + "_"):
                    tex[(slot, st[len(slot) + 1:])] = p
        for k in missing:
            if k in tex:
                shutil.copyfile(tex[k], img / ("%s-%s.png" % k))
                kind[k] = "model texture"
    print("  pictures ready for %d of %d items" % (len(kind), len(wanted)))
    known = {n[0].lower(): n for n in names}
    for internal, english in load_my_names().items():  # your own names win for every slot, not only headgear
        known[internal] = (internal, english, "mine")
    cards = []
    counts = {slot: 0 for slot in SLOTS}
    for (slot, internal) in sorted(wanted, key=lambda k: (known.get(k[1], ("", k[1]))[1].lower(), k[0])):
        english = known.get(internal, (internal, internal, ""))[1]
        note = kind.get((slot, internal))
        counts[slot] += 1
        pic = '<img loading="lazy" src="img/%s-%s.png">' % (slot, internal) if note is not None else '<div class="none">no picture</div>'
        cards.append('<div class="c" data-s="%s" data-k="%s" data-i="%s" data-e="%s">%s<b>%s</b><small>%s</small>%s</div>' % (
            slot, html.escape((english + " " + internal).lower()), html.escape(internal), html.escape(english), pic,
            html.escape(english), html.escape(internal), ("<small class=n>%s</small>" % note) if note else ""))
    tabs = "".join('<button class="t" data-s="%s">%s (%d)</button>' % (slot, SLOTS[slot][0], counts[slot]) for slot in SLOTS)
    page = """<!doctype html><meta charset="utf-8"><title>Origin items</title>
<style>body{font:14px Segoe UI,Arial;margin:0;background:#1d1f24;color:#eee}#top{position:sticky;top:0;background:#111;padding:10px;z-index:2}
#q{width:260px;padding:6px}#line{width:420px;padding:6px;font-family:Consolas,monospace}#g{display:flex;flex-wrap:wrap;gap:8px;padding:10px}
.t{padding:6px 12px;margin-right:4px;border:0;border-radius:4px;background:#2a2d34;color:#eee;cursor:pointer}.t.on{background:#4a78c9}
.c{width:132px;background:#2a2d34;border-radius:6px;padding:6px;text-align:center;cursor:pointer}.c:hover{background:#3b4150}
.c img{width:96px;height:96px;object-fit:contain}.none{height:96px;line-height:96px;color:#888}.c b{display:block;font-size:12px}.c small{display:block;color:#9aa}.n{color:#db7!important}</style>
<div id="top">""" + tabs + """ &nbsp; Search: <input id="q" placeholder="type part of a name" autofocus> &nbsp; <span id="n"></span><br>
Click an item: its name is selected in the box below, so just type your name over it, press <b>Copy line</b>, and paste it into <b>my-hat-names.csv</b> (one line per item). Orange note = the picture is a stand-in:<br>
<input id="line" placeholder="click an item"> <button id="cp">Copy line</button> <span id="ok"></span></div><div id="g">""" + "".join(cards) + """</div>
<script>
var cs=[].slice.call(document.querySelectorAll('.c')),ts=[].slice.call(document.querySelectorAll('.t')),q=document.getElementById('q'),n=document.getElementById('n'),l=document.getElementById('line');
var tab='helmet';
function f(){var w=q.value.toLowerCase().split(/\\s+/).filter(Boolean),c=0;cs.forEach(function(e){var ok=e.dataset.s===tab&&w.every(function(x){return e.dataset.k.indexOf(x)>=0});e.style.display=ok?'':'none';if(ok)c++});n.textContent=c+' shown';ts.forEach(function(t){t.className='t'+(t.dataset.s===tab?' on':'')})}
ts.forEach(function(t){t.onclick=function(){tab=t.dataset.s;f()}});
q.oninput=f;f();var ok=document.getElementById('ok');
cs.forEach(function(e){e.onclick=function(){var a=e.dataset.i+',';l.value=a+e.dataset.e;l.focus();l.setSelectionRange(a.length,l.value.length);ok.textContent=''}});
document.getElementById('cp').onclick=function(){l.select();var done=false;try{done=document.execCommand('copy')}catch(x){}
if(!done&&navigator.clipboard){navigator.clipboard.writeText(l.value);done=true}ok.textContent=done?'copied - paste it into my-hat-names.csv':'press Ctrl+C'};
</script>"""
    (out / "hats.html").write_text(page, encoding="utf-8")
    print("  gallery page: %s  (%d items)" % (out / "hats.html", len(cards)))
    opener = getattr(os, "startfile", None)
    if opener:
        opener(str(out / "hats.html"))
        print("  (opened in your browser)")


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def yes(prompt):
    return ask(prompt + " (y/n): ").lower().startswith("y")


def show_picture(path):
    print("  icon picture: %s" % path)
    opener = getattr(os, "startfile", None)
    if opener:
        try:
            opener(str(path))
            print("  (I opened it for you. If no window appeared, open that file yourself.)")
        except OSError:
            pass


def run_build(args):
    print("\nBuilding the custom items ...\n")
    r = subprocess.run([sys.executable, str(HERE / "build_custom_items.py"), "--client", args.client])
    if r.returncode != 0:
        die("the build step failed - see above (your hat files and the CSV row are already in place; fix the problem and run Build-CustomItems.cmd)")


def pick_slot(args):
    if args.slot:
        return args.slot
    print("\nWhat do you want to import?")
    order = list(SLOT_MAP)
    for n, s in enumerate(order, start=1):
        print("  %d)  %s" % (n, SLOT_LABEL[s]))
    while True:
        choice = ask("\nNumber (Enter = Headwear) > ")
        if not choice:
            return "head"
        if choice.isdigit() and 1 <= int(choice) <= len(order):
            return order[int(choice) - 1]


def interactive(cfg, names, idx, args):
    imported = 0
    cfg.slot = pick_slot(args)
    if cfg.slot != "head":
        print("Reading the %s list from the Origin file index ..." % SLOT_LABEL[cfg.slot])
        idx = Index(cfg.index, SLOT_MAP[cfg.slot])
        names = slot_names(idx)
    elif idx.slot != "helmet":
        idx = Index(cfg.index, "helmet")
    done = {(r.get("OriginHat") or "").strip().lower(): r for r in cfg.rows if row_slot(r) == cfg.slot}
    print("\n=== Import an Origin item: %s ===" % SLOT_LABEL[cfg.slot])
    if cfg.slot == "head":
        print("Type the English name of the hat (or part of it). Press Enter on an empty line to finish.\n")
    else:
        print("Type a part of the Origin name (the gallery page shows them: Browse-Hats.cmd, tab %s). Enter on an empty line = finish.\n" % SLOT_LABEL[cfg.slot])
    while True:
        query = ask("Name > ")
        if not query:
            break
        results = search(query, names)
        if not results:
            print("  Nothing found for '%s'. Try fewer words (or run Browse-Hats.cmd and look for it by picture).\n" % query)
            continue
        shown = results[:30]
        print()
        for number, (internal, english, _conf) in enumerate(shown, start=1):
            tag = ""
            if not idx.model(internal):
                tag = "   [cannot import: no model in Origin files]"
            elif internal.lower() in done:
                tag = "   [already in your list as %s]" % done[internal.lower()]["Key"]
            print("  %2d)  %s%s" % (number, english, tag))
        if len(results) > len(shown):
            print("  ... %d more - type more words to narrow it down" % (len(results) - len(shown)))
        choice = ask("\nNumber of the hat (or Enter to search again) > ")
        if not choice.isdigit() or not 1 <= int(choice) <= len(shown):
            print()
            continue
        internal, english, _conf = shown[int(choice) - 1]
        model_row = idx.model(internal)
        if not model_row:
            print("  That hat has no model in the Origin files, so it cannot be imported.\n")
            continue
        existing = done.get(internal.lower())
        print("\n  Looking up %s ..." % english)
        work = Path(tempfile.mkdtemp(prefix="import-hat-"))
        try:
            obj, tex = export_model(cfg, idx.parts(internal), work)
            if not obj or not tex:
                print("  could not get a mesh and a texture for this hat (mesh: %s, texture: %s) - skipped\n" % ("yes" if obj else "NO", "yes" if tex else "NO"))
                continue
            icon_row, icon_name, exact = idx.icon(internal)
            icon_png = extract_icon(cfg, icon_row, work)
            preview = HERE / "preview"
            preview.mkdir(exist_ok=True)
            target = preview / (internal + ".png")
            if icon_png:
                shutil.copyfile(icon_png, target)
                if not exact:
                    print("  (no icon with the exact name; showing the similar icon '%s' - check it)" % icon_name)
            else:
                shutil.copyfile(tex, target)
                print("  (no inventory icon found for this hat; showing the model texture instead)")
            show_picture(target)
            question = ("  It is already in your list as %s. Extract its files again (your size settings are kept)?" % existing["Key"]) if existing else "  Is this the hat you want?"
            if not yes(question):
                print("  OK, not imported.\n")
                continue
            if import_one(cfg, internal, english, icon_png, obj, tex, existing):
                imported += 1
                if not existing:
                    done[internal.lower()] = cfg.rows[-1]
                print("  Done.\n")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if imported:
        run_build(args)
        print("\nNext: start the game with")
        print("  cd \"C:\\Users\\donor\\Documents\\RO3-Studio\\RO3 OFFICIAL SERVER FILES V2\"; .\\scripts\\start.cmd -ClientDir %s" % args.client)
    else:
        print("\nNothing was imported.")


def main():
    ap = argparse.ArgumentParser(description="Import Origin hats as custom items")
    ap.add_argument("hats", nargs="*", help="English name or Origin name (several allowed); leave empty for the question mode")
    ap.add_argument("--list", metavar="WORD", help="search the English list and exit")
    ap.add_argument("--slot", choices=sorted(SLOT_MAP), help="head (default), face, mouth or back: what kind of item to import")
    ap.add_argument("--client", default=r"C:\Games\Ragnarok3-Official")
    ap.add_argument("--index", default="", help="Origin file index (roo-index2.csv); default: <client>\ro3emu\roo-index2.csv, else the one in C:\Games\EclipseClient")
    ap.add_argument("--origin", default=r"C:\Game\RooGameSEALauncher\RooGame\ro_Data\StreamingAssets\AssetBundles\BLOCK")
    ap.add_argument("--assetstudio", default=r"C:\Tools\AssetStudio\AssetStudioModCLI.exe")
    ap.add_argument("--anim", metavar="HAT", help="copy the Origin bundles of an imported hat so it can be ANIMATED in game (opt-in, per hat)")
    ap.add_argument("--inspect", metavar="HAT", help="export everything readable from an imported hat's Origin bundles (for animation analysis)")
    ap.add_argument("--peek", metavar="HAT", help="read-only: export everything readable of ANY Origin hat (also one that cannot be imported yet)")
    ap.add_argument("--gallery", action="store_true", help="make a picture page of all hats (find a hat by looking)")
    ap.add_argument("--no-build", action="store_true", help="do not run build_custom_items.py at the end")
    args = ap.parse_args()
    names = load_names()
    if args.list and not args.slot or args.list and args.slot == "head":
        hits = search(args.list, names)
        for n in hits:
            print("%-34s %-26s %s" % (n[1], n[0], n[2]))
        print("%d hat(s)" % len(hits))
        return
    cfg = Setup()
    cfg.slot = args.slot or "head"
    cfg.client = Path(args.client)
    cfg.index = Path(args.index) if args.index else cfg.client / "ro3emu" / "roo-index2.csv"
    if not args.index and not cfg.index.is_file():
        cfg.index = Path(r"C:\Games\EclipseClient\ro3emu\roo-index2.csv")  # the Official client does not carry the index
    cfg.origin = args.origin
    cfg.assetstudio = args.assetstudio
    for need, what in ((cfg.index, "the Origin file index"), (Path(args.assetstudio), "AssetStudioModCLI.exe"), (Path(args.origin), "the Origin BLOCK folder")):
        if not need.exists():
            die("cannot find %s: %s" % (what, need))
    if not ITEMS_CSV.is_file():
        die("cannot find " + str(ITEMS_CSV))
    cfg.rows, cfg.fields, cfg.bom = read_items()
    cfg.taken = {r["Key"].lower() for r in cfg.rows}
    print("Reading the Origin file index ...")
    idx = Index(cfg.index, SLOT_MAP[cfg.slot])
    if cfg.slot != "head":
        names = slot_names(idx)
    if args.list:  # a non-head slot
        for n in search(args.list, names):
            print("%-34s %s" % (n[1], n[0]))
        return
    if args.gallery:
        gallery(cfg, names, idx)
        return
    if args.peek:
        r = resolve(args.peek, names)
        if not r:
            return
        print("== peeking at %s (%s)" % (r[1], r[0]))
        peek(cfg, r[0], SLOT_MAP[cfg.slot])
        return
    if args.inspect:
        r = resolve(args.inspect, names)
        if not r:
            return
        internal, english = r
        mine = [row for row in cfg.rows if (row.get("OriginHat") or "").strip().lower() == internal.lower()]
        if not mine:
            die("%s is not imported yet" % english)
        print("== inspecting %s" % english)
        inspect(cfg, internal, mine[0]["Key"])
        return
    if args.anim:
        r = resolve(args.anim, names)
        if not r:
            return
        internal, english = r
        mine = [row for row in cfg.rows if (row.get("OriginHat") or "").strip().lower() == internal.lower()]
        if not mine:
            die("%s is not imported yet - import it first (Import-Hat.cmd), then run this again" % english)
        key = mine[0]["Key"]
        print("== %s -> hat-anim\\%s" % (english, key))
        if not export_anim(cfg, internal, key):
            die("no Unity bundles found for this hat")
        print("\nDone. Start the game (Update-And-Play.cmd) and check Player.log for lines with [Ro3Emu.HatAnim].")
        return
    if not args.hats:
        interactive(cfg, names, idx, args)
        return
    imported = 0
    done = {(r.get("OriginHat") or "").strip().lower(): r for r in cfg.rows if row_slot(r) == cfg.slot}
    for a in args.hats:
        r = resolve(a, names)
        if not r:
            continue
        internal, english = r
        print("\n== %s  (%s)" % (english, internal))
        model_row = idx.model(internal)
        if not model_row:
            print("  no model for this hat in the Origin files - skipped")
            continue
        work = Path(tempfile.mkdtemp(prefix="import-hat-"))
        try:
            obj, tex = export_model(cfg, idx.parts(internal), work)
            if not obj or not tex:
                print("  could not get a mesh and a texture for this hat (mesh: %s, texture: %s) - skipped" % ("yes" if obj else "NO", "yes" if tex else "NO"))
                continue
            icon_png = extract_icon(cfg, idx.icon(internal)[0], work)
            if import_one(cfg, internal, english, icon_png, obj, tex, done.get(internal.lower())):
                imported += 1
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if imported and not args.no_build:
        run_build(args)
    elif imported:
        print("\nImported %d hat(s). Now run Build-CustomItems.cmd." % imported)
    else:
        print("\nNothing was imported.")


if __name__ == "__main__":
    main()
