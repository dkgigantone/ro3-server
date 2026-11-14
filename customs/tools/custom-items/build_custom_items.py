#!/usr/bin/env python3
"""Builds the custom items (hats) of Ro3Emu from custom-items.csv.

Edit custom-items.csv (Excel or Notepad), then run  Build-CustomItems.cmd  (or: python build_custom_items.py).
Close the game and the server first. Use --check to see what would change without writing anything.

What it writes:
  server  data\\config\\ItemConfig.json, WardRobeConfig.json, TradeConfig.json  (only the custom rows; everything else
          in those files is left byte for byte as it was; the three files are backed up first)
  client  <client>\\ro3emu\\custom\\items.tsv           the list the game hook reads (names, style bonus, rarity, stall)
          <client>\\ro3emu\\custom\\models\\*.alias     model aliases (donor: Banana Hat)
          <client>\\ro3emu\\hats\\<Key>M/F.txt          default hat settings if a model exists but has no settings yet
  It also renames files of a hat whose Key changed (column RenamedFrom) and removes stale alias files.
"""
import argparse
import copy
import csv
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ITEM_FIRST, ITEM_LAST = 3004901, 3004999          # item IDs and market rows this tool manages
TEXT_BASE_FIRST = 99910000001                     # text IDs: base + (ItemID - ITEM_FIRST) * 10 + 0 name, 1 look text, 2 item text
# One entry per wardrobe slot. Every custom item is a copy of that slot's donor (a game item whose prefab we borrow; the
# real look is swapped in by the game hook). token = the part of the donor's file names that becomes the item's own name.
#   genders: the donor has a male (m) and female (f) model, or one model for both ("")
#   icon_end: what follows the name in the wardrobe icon file name; type: key of the game's wardrobe list for the slot
SLOTS = {
    "head": dict(item="3004111", ward="10040434", token="bananahat", prefix="headaccs", genders=("m", "f"), icon_end="",
                 ward_first=10049001, ward_last=10049099, type=1),
    "face": dict(item="3004206", ward="20040412", token="hearteyepatch", prefix="faceaccs", genders=("m", "f"), icon_end="m",
                 ward_first=20049001, ward_last=20049099, type=2),
    "mouth": dict(item="3004301", ward="30040404", token="clownnose", prefix="mouthaccs", genders=("m", "f"), icon_end="m",
                  ward_first=30049001, ward_last=30049099, type=3),
    "back": dict(item="3004504", ward="50040401", token="cschoolbagrde", prefix="backaccs", genders=("",), icon_end="",
                 ward_first=50049001, ward_last=50049099, type=5),
}
RARITY = {"normal": 3, "advanced": 4, "collector's": 5, "collectors": 5, "luxury": 6}
DEFAULT_TXT = "scale=1\r\nrotate=-82.34,0.2,0\r\noffset=0,0,0\r\nflipv=0\r\nmirrorx=0\r\n"
SUFFIXES = ("dots.prefab", "lod0.asset", "lod0.png", "lodx.asset", "lodx.png")
TABLES = ("ItemConfig", "WardRobeConfig", "TradeConfig")


class Hat:
    pass


def fail(errors):
    print("\nNOT BUILT - fix these in custom-items.csv first:")
    for e in errors:
        print("  - " + e)
    sys.exit(1)


def to_int(text, what, row, errors, lo=None, hi=None):
    try:
        value = int(str(text).strip())
    except ValueError:
        errors.append("row %d: %s must be a whole number (got '%s')" % (row, what, text))
        return None
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        errors.append("row %d: %s must be between %s and %s (got %d)" % (row, what, lo, hi, value))
        return None
    return value


def to_bool(text, row, errors):
    t = str(text).strip().lower()
    if t in ("true", "yes", "1"):
        return True
    if t in ("false", "no", "0"):
        return False
    errors.append("row %d: Stall must be True or False (got '%s')" % (row, text))
    return None


def read_csv(path):
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    names = reader.fieldnames or []
    for need in ("Key", "ItemName", "StyleBonus", "Rarity", "Stall", "StallPrice", "ItemID", "WardrobeID"):
        if need not in names:
            fail(["column '%s' is missing from the header of %s" % (need, path.name)])
    rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return rows, names, bom


def parse_hats(rows):
    errors, hats = [], []
    seen_keys, seen_items, seen_wards = set(), set(), set()
    for number, r in enumerate(rows, start=2):
        h = Hat()
        h.row = number
        h.key = (r.get("Key") or "").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,40}", h.key):
            errors.append("row %d: Key '%s' must be letters and digits only, no spaces (e.g. CatEarBeret)" % (number, h.key))
        elif h.key.lower() in seen_keys:
            errors.append("row %d: Key '%s' is used twice" % (number, h.key))
        seen_keys.add(h.key.lower())
        h.name = (r.get("ItemName") or "").strip()
        if not h.name or len(h.name) > 60 or any(c in h.name for c in "\t\r\n"):
            errors.append("row %d: ItemName must be 1-60 characters, no tabs" % number)
        h.slot = (r.get("Slot") or "head").strip().lower()
        if h.slot not in SLOTS:
            errors.append("row %d: Slot must be one of %s (got '%s')" % (number, ", ".join(SLOTS), h.slot))
            h.slot = "head"
        h.cfg = SLOTS[h.slot]
        h.desc = (r.get("Description") or "").strip() or "A custom item."
        if any(c in h.desc for c in "\t\r\n"):
            errors.append("row %d: Description must be one line without tabs" % number)
        h.origin = (r.get("OriginHat") or "").strip()
        h.renamed_from = (r.get("RenamedFrom") or "").strip()
        h.style = to_int(r.get("StyleBonus", ""), "StyleBonus", number, errors, 10, 1000)
        rarity = (r.get("Rarity") or "").strip().lower()
        h.rarity = RARITY.get(rarity)
        if h.rarity is None:
            errors.append("row %d: Rarity must be Normal, Advanced, Collector's or Luxury (got '%s')" % (number, r.get("Rarity")))
        h.stall = to_bool(r.get("Stall", ""), number, errors)
        price = (r.get("StallPrice") or "").strip()
        h.price = to_int(price, "StallPrice", number, errors, 1, 999999999) if (price or h.stall) else 0
        h.item_id = to_int(r["ItemID"], "ItemID", number, errors, ITEM_FIRST, ITEM_LAST) if (r.get("ItemID") or "").strip() else None
        h.ward_id = to_int(r["WardrobeID"], "WardrobeID", number, errors, h.cfg["ward_first"], h.cfg["ward_last"]) if (r.get("WardrobeID") or "").strip() else None
        if h.item_id is not None and h.item_id in seen_items:
            errors.append("row %d: ItemID %s is used twice" % (number, h.item_id))
        if h.ward_id is not None and h.ward_id in seen_wards:
            errors.append("row %d: WardrobeID %s is used twice" % (number, h.ward_id))
        seen_items.add(h.item_id)
        seen_wards.add(h.ward_id)
        hats.append(h)
    if errors:
        fail(errors)
    # fill blank IDs: next free ones
    used_items = {h.item_id for h in hats if h.item_id}
    used_wards = {h.ward_id for h in hats if h.ward_id}
    filled = False
    for h in hats:
        if h.item_id is None:
            h.item_id = next(i for i in range(ITEM_FIRST, ITEM_LAST + 1) if i not in used_items)
            used_items.add(h.item_id)
            filled = True
        if h.ward_id is None:
            first, last = h.cfg["ward_first"], h.cfg["ward_last"]
            guess = first + (h.item_id - ITEM_FIRST)
            h.ward_id = guess if (guess not in used_wards and guess <= last) else next(i for i in range(first, last + 1) if i not in used_wards)
            used_wards.add(h.ward_id)
            filled = True
        h.text_base = TEXT_BASE_FIRST + (h.item_id - ITEM_FIRST) * 10
        h.asset = h.key.lower()
        h.item_desc = "Use to unlock the %s appearance in the wardrobe." % h.name
    return hats, filled


# ---------- server tables (text level edits: the rest of each file stays byte for byte) ----------

DEC = json.JSONDecoder()


def top_level_spans(text):
    """key -> (key_start, value_start, value_end) of every top-level entry of a JSON object."""
    spans = {}
    i = text.index("{") + 1
    while True:
        while text[i] in " \r\n\t,":
            i += 1
        if text[i] == "}":
            return spans
        key, kend = DEC.raw_decode(text, i)
        j = kend
        while text[j] in " \r\n\t":
            j += 1
        assert text[j] == ":"
        j += 1
        while text[j] in " \r\n\t":
            j += 1
        _, vend = DEC.raw_decode(text, j)
        spans[key] = (i, j, vend)
        i = vend


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_rows(hats, templates):
    """templates: slot -> (item row, wardrobe row, market row) of that slot's donor."""
    items, wards, trades = {}, {}, {}
    for index, h in enumerate(hats, start=1):
        item_t, ward_t, trade_t = templates[h.slot]
        w = copy.deepcopy(ward_t)
        for k, v in list(w.items()):
            if isinstance(v, str) and h.cfg["token"] in v.lower():
                w[k] = re.sub(h.cfg["token"], h.asset, v, flags=re.I)
        w.update({"_iID": h.ward_id, "_iName": [h.text_base], "_iDescription": [h.text_base + 1], "_iSort": index,
                  "_iFashionScore": h.style, "_iQuality": h.rarity, "_kIcon": "item_%d.png" % h.item_id})
        wards[str(h.ward_id)] = w
        i = copy.deepcopy(item_t)
        i.update({"_iID": h.item_id, "_iSort": h.item_id, "_iName": [h.text_base], "_iDescription": [h.text_base + 2],
                  "_kIcon": "item_%d.png" % h.item_id, "_iUseEffect": [["200", "0", str(h.ward_id)]], "_kDecompose": {},
                  "_iQuality": h.rarity, "_iTrade": item_t["_iTrade"] if h.stall else 0})
        items[str(h.item_id)] = i
        if h.stall:
            t = copy.deepcopy(trade_t)
            t.update({"_iID": h.item_id, "_kSpuName": h.name, "_iBasePrice": h.price})
            trades[str(h.item_id)] = t
    return {"ItemConfig": items, "WardRobeConfig": wards, "TradeConfig": trades}


def edit_table(text, desired, first, last, anchor):
    """Returns (new_text, added, updated, removed). Only keys in [first, last] are touched."""
    spans = top_level_spans(text)
    whole = json.loads(text)
    edits, added, updated, removed = [], [], [], []
    managed = [k for k in spans if k.isdigit() and first <= int(k) <= last]
    for k in managed:
        ks, vs, ve = spans[k]
        if k not in desired:
            edits.append((ks - 1, ve, ""))  # the comma before the entry goes too
            removed.append(k)
        elif whole[k] != desired[k]:
            edits.append((vs, ve, dumps(desired[k])))
            updated.append(k)
    new_keys = [k for k in desired if k not in spans]
    if new_keys:
        at = max([spans[k][2] for k in managed] + [spans[anchor][2]])
        edits.append((at, at, "".join(',"%s":%s' % (k, dumps(desired[k])) for k in new_keys)))
        added = new_keys
    for start, end, repl in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        text = text[:start] + repl + text[end:]
    check = json.loads(text)
    expect = {k: v for k, v in whole.items() if k not in managed}
    expect.update(desired)
    assert set(check) == set(expect) and all(check[k] == expect[k] for k in expect), "internal check failed: " + anchor
    return text, added, updated, removed


# ---------- client files ----------

def find_ci(folder, name):
    """The real file name in folder that equals name ignoring case, or None."""
    if not folder.is_dir():
        return None
    for f in os.listdir(folder):
        if f.lower() == name.lower():
            return f
    return None


def main():
    here = Path(__file__).resolve().parent
    repo = here.parents[2]  # customs\tools\custom-items -> the V2 repository root (data\config lives there)
    ap = argparse.ArgumentParser(description="Build the custom items from custom-items.csv")
    ap.add_argument("--check", action="store_true", help="show what would change, write nothing")
    ap.add_argument("--csv", default=str(here / "custom-items.csv"))
    ap.add_argument("--client", default=r"C:\Games\Ragnarok3-Official")
    ap.add_argument("--server-data", default=str(repo / "data" / "config"))
    ap.add_argument("--backup-root", default=str(repo / "staging" / "backup-custom-items"))
    args = ap.parse_args()
    dry = args.check
    csv_path, data_dir, client = Path(args.csv), Path(args.server_data), Path(args.client)
    if not csv_path.is_file():
        fail(["cannot find " + str(csv_path)])
    if not data_dir.is_dir():
        fail(["cannot find the server tables folder " + str(data_dir)])
    if not (client / "ro3emu").is_dir():
        fail(["cannot find " + str(client / "ro3emu") + " (use --client <game folder>)"])

    rows, fieldnames, bom = read_csv(csv_path)
    hats, filled = parse_hats(rows)
    print("%s: %d custom item(s)%s" % (csv_path.name, len(hats), "  [CHECK ONLY - nothing is written]" if dry else ""))

    # ---- server tables
    texts, parsed = {}, {}
    for t in TABLES:
        p = data_dir / (t + ".json")
        texts[t] = p.read_bytes().decode("utf-8")
        parsed[t] = json.loads(texts[t])
    templates = {}
    for slot, c in SLOTS.items():
        for table, key in (("ItemConfig", c["item"]), ("WardRobeConfig", c["ward"]), ("TradeConfig", c["item"])):
            if key not in parsed[table]:
                fail(["the donor row %s of the %s slot is not in %s.json" % (key, slot, table)])
        templates[slot] = (parsed["ItemConfig"][c["item"]], parsed["WardRobeConfig"][c["ward"]], parsed["TradeConfig"][c["item"]])
    desired = build_rows(hats, templates)
    # (table, first ID, last ID, anchor row to insert after, only the rows of this range)
    spec = [("ItemConfig", ITEM_FIRST, ITEM_LAST, SLOTS["head"]["item"]), ("TradeConfig", ITEM_FIRST, ITEM_LAST, SLOTS["head"]["item"])]
    spec += [("WardRobeConfig", c["ward_first"], c["ward_last"], c["ward"]) for c in SLOTS.values()]
    changed = {}
    for table, first, last, anchor in spec:
        mine = {k: v for k, v in desired[table].items() if first <= int(k) <= last}
        new_text, added, updated, removed = edit_table(changed.get(table, texts[table]), mine, first, last, anchor)
        print("  %-15s %d-%d  +%d added  ~%d updated  -%d removed  (%d custom rows)" % (table, first, last, len(added), len(updated), len(removed), len(mine)))
        if added or updated or removed:
            changed[table] = new_text
    if changed and not dry:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = Path(args.backup_root) / stamp
        backup.mkdir(parents=True, exist_ok=True)
        for t in changed:
            shutil.copyfile(data_dir / (t + ".json"), backup / (t + ".json"))
            (data_dir / (t + ".json")).write_bytes(changed[t].encode("utf-8"))
        print("  server tables written; the old ones are in " + str(backup))

    # ---- client files
    ro = client / "ro3emu"
    hats_dir, icons, models, custom = ro / "hats", ro / "custom" / "icons", ro / "custom" / "models", ro / "custom"
    actions, warnings = [], []

    def move_ci(folder, old, new):
        have = find_ci(folder, new)
        if have == new:
            return
        if have is not None:  # same name, other case
            actions.append("rename %s -> %s" % (have, new))
            if not dry:
                tmp = folder / (have + ".tmp-rename")
                os.rename(folder / have, tmp)
                os.rename(tmp, folder / new)
            return
        src = find_ci(folder, old) if old else None
        if src:
            actions.append("rename %s -> %s" % (src, new))
            if not dry:
                os.rename(folder / src, folder / new)

    wanted_alias = set()
    for h in hats:
        c = h.cfg
        genders = c["genders"]
        icon_name = "wardrobe_icon_%s_%s%s.png" % (c["prefix"], h.asset, c["icon_end"])
        for g in genders:
            for ext in (".obj", ".png", ".txt"):
                move_ci(hats_dir, (h.renamed_from + g.upper() + ext) if h.renamed_from else None, h.key + g.upper() + ext)
        if h.renamed_from and (ro / "hat-anim").is_dir():
            move_ci(ro / "hat-anim", h.renamed_from, h.key)
        move_ci(icons, ("wardrobe_icon_%s_%s%s.png" % (c["prefix"], h.renamed_from.lower(), c["icon_end"])) if h.renamed_from else None,
                icon_name)
        for g in genders:
            for suf in SUFFIXES:
                name = "model_%s_%s%s_%s.alias" % (c["prefix"], h.asset, g, suf)
                wanted_alias.add(name.lower())
                donor = "model_%s_%s%s_%s" % (c["prefix"], c["token"], g, suf)
                body = donor + "\r\n" + (("%s=%s\r\n" % (c["token"], h.key)) if suf == "dots.prefab" else "")
                target = models / name
                if not target.is_file() or target.read_bytes().decode("utf-8") != body:
                    actions.append("write alias " + name)
                    if not dry:
                        models.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(body.encode("utf-8"))
        for g in (x.upper() for x in genders):
            obj = find_ci(hats_dir, h.key + g + ".obj")
            if not obj:
                warnings.append("%s: no model file hats\\%s%s.obj yet (import the hat first)" % (h.key, h.key, g))
            elif not find_ci(hats_dir, h.key + g + ".txt"):
                actions.append("write default hats\\%s%s.txt" % (h.key, g))
                if not dry:
                    (hats_dir / (h.key + g + ".txt")).write_bytes(DEFAULT_TXT.encode("utf-8"))
            if obj and not find_ci(hats_dir, h.key + g + ".png"):
                warnings.append("%s: hats\\%s%s.png (texture) is missing" % (h.key, h.key, g))
        for needed in ("item_%d.png" % h.item_id, icon_name):
            if not find_ci(icons, needed):
                warnings.append("%s: icon custom\\icons\\%s is missing" % (h.key, needed))
    # stale alias files this tool made earlier (donor Banana Hat) for hats that are gone or renamed
    if models.is_dir():
        for f in os.listdir(models):
            donors = tuple("model_%s_%s" % (c["prefix"], c["token"]) for c in SLOTS.values())
            if f.lower().startswith(tuple("model_%s_" % c["prefix"] for c in SLOTS.values())) and f.lower().endswith(".alias") and f.lower() not in wanted_alias:
                first_line = (models / f).read_text(encoding="utf-8", errors="replace").splitlines()[:1]
                if first_line and first_line[0].startswith(donors):
                    actions.append("remove stale alias " + f)
                    if not dry:
                        (models / f).unlink()
    # the list the game hook reads
    lines = ["# written by build_custom_items.py - do not edit; edit custom-items.csv instead",
             "# Key\tItemID\tWardrobeID\tTextBase\tAsset\tStyleBonus\tRarity\tStall\tBasePrice\tName\tLookText\tItemText"
             "\tSlot\tDonorWardrobe\tDonorItem\tDonorToken\tTypeList"]
    for h in hats:
        lines.append("\t".join([h.key, str(h.item_id), str(h.ward_id), str(h.text_base), h.asset, str(h.style), str(h.rarity),
                                "1" if h.stall else "0", str(h.price), h.name, h.desc, h.item_desc,
                                h.slot, h.cfg["ward"], h.cfg["item"], h.cfg["token"], str(h.cfg["type"])]))
    tsv = ("\r\n".join(lines) + "\r\n").encode("utf-8")
    tsv_path = custom / "items.tsv"
    if not tsv_path.is_file() or tsv_path.read_bytes() != tsv:
        actions.append("write custom\\items.tsv (%d hats)" % len(hats))
        if not dry:
            custom.mkdir(parents=True, exist_ok=True)
            tsv_path.write_bytes(tsv)
    # hat files nobody owns (information only)
    if hats_dir.is_dir():
        owned = {(h.key + g.upper()).lower() for h in hats for g in h.cfg["genders"]}
        orphans = sorted({Path(f).stem for f in os.listdir(hats_dir) if f.lower().endswith(".obj") and Path(f).stem.lower() not in owned})
        if orphans:
            warnings.append("item files (hats folder) not in the CSV (left alone): " + ", ".join(orphans))
    for a in actions:
        print("  client: " + a)
    if not actions:
        print("  client: nothing to change")
    for w in warnings:
        print("  WARNING: " + w)

    # ---- fill blank IDs back into the CSV
    if filled:
        by_row = {h.row: h for h in hats}
        for number, r in enumerate(rows, start=2):
            r["ItemID"], r["WardrobeID"] = str(by_row[number].item_id), str(by_row[number].ward_id)
        print("  IDs assigned: " + ", ".join("%s=%d/%d" % (h.key, h.item_id, h.ward_id) for h in hats))
        if not dry:
            with open(csv_path, "w", newline="", encoding="utf-8-sig" if bom else "utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\r\n", extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
    print("\nDone." if not dry else "\nCheck finished - nothing was written.")
    if not dry:
        print("Next: start the server and the game (scripts\\start.cmd -ClientDir " + str(client) + ").")


if __name__ == "__main__":
    main()
