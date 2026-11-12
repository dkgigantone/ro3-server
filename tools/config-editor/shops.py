"""Shop editing for the RO3 Plus Config Editor (Shops page).

Your edits live in overrides/shops.json (only what you changed). "Apply" rebuilds ShopCommodityConfig.json =
untouched original + your overrides, with a backup first. The server reads data/config once at startup, so restart it.

What can be edited today (existing rows only)
  * Purchase limit      ShopCommodityConfig._iPurchaseNum         (0 = no limit)
  * Limit period        ShopCommodityConfig._iPurchaseTimesLimit  (1 daily, 2 weekly, 3 monthly, 4 lifetime, 5 season)
  * Currency and price  ShopCommodityConfig._kItemPerPrice        [currency item id, list price]
  * Item sold           ShopCommodityConfig._iItemId              (item rows only; fashion "look" rows are read-only)
The server charges from its own table (ShopService.BuyOneAsync); the discount (_iDiscount, basis points) is applied on
top of the list price, so the page shows what a player pays too.
Standard library only.
"""
import csv
import hashlib
import io
import json
import shutil
from datetime import datetime
from pathlib import Path

import common
from common import APP_DIR, LOCK, STATE_DIR, atomic_write, compact, read_json, server_running, sha256, update_overlay

OVERRIDES = APP_DIR / "overrides" / "shops.json"
TABLE = "ShopCommodityConfig"
READ_TABLES = ("ShopConfig", "ItemConfig", "WardRobeConfig", "Localization_en")
MAX_BACKUPS = 5
MAX_LIMIT = 99_999
MAX_PRICE = 2_000_000_000

# enum_ShopLimitType as read by ShopService.Family/Period
PERIODS = {0: "none", 1: "daily", 2: "weekly", 3: "monthly", 4: "lifetime", 5: "season"}
PERIOD_BY_NAME = {v: k for k, v in PERIODS.items()}

# Page tabs -> ShopConfig._iClassID values (the order the page shows them in)
GROUPS = (
    ("Fashion", (501, 50201, 50202, 50203, 50204, 50207, 50208)),
    ("Shop", (120, 130, 135, 1401, 1402)),
    ("Point Mall", (210, 220, 230, 240, 250, 260, 2701, 2702)),
)
# LIKELY (from the look ids of the rows; not read from the client): which costume class is which
FASHION_HINTS = {501: "no rows in the data", 50201: "likely set", 50202: "likely mount", 50203: "likely hairstyle",
                 50204: "likely headwear", 50207: "likely backwear", 50208: "likely character effects"}

K_PRICE, K_NUM, K_PER, K_ITEM = "_kItemPerPrice", "_iPurchaseNum", "_iPurchaseTimesLimit", "_iItemId"
FIELD_KEYS = {"num": K_NUM, "per": K_PER, "cur": K_PRICE, "price": K_PRICE, "itemId": K_ITEM}


def _index(data):
    """id -> row for a table stored as an id -> row dict or as a list of rows with _iID."""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    return {str(r["_iID"]): r for r in data if isinstance(r, dict) and "_iID" in r}


def _text(loc, ref):
    """The English text of a text reference ([id] list or a bare id) from Localization_en."""
    tid = ref[0] if isinstance(ref, list) and ref else ref
    entry = loc.get(str(tid)) if isinstance(tid, int) and tid else None
    return entry.get("_kDes", "") if isinstance(entry, dict) else ""


def _int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{what} must be a whole number.")
    return value


def _whole(text, what):
    try:
        return int(text.replace(",", ""))
    except ValueError:
        raise ValueError(f"{what} '{text}' is not a whole number")


def pays(price, disc):
    """The unit price ShopService.UnitPrice charges: list price x discount/10000 (rounded up) when a discount is set."""
    if disc and disc > 0:
        return -(-price * disc // 10000)
    return price


class ShopsEditor:
    def __init__(self, home: Path):
        self.home = home
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.original = self.state_dir / "shops_originals" / f"{TABLE}.json"
        self.backups = self.state_dir / "shops_backups"
        self.state_file = self.state_dir / "shops_state.json"
        self.cache = None
        self.base = None

    # ------------------------------------------------------------ paths and state
    @property
    def config_dir(self):
        return self.home / "data" / "config"

    @property
    def live(self):
        return self.config_dir / f"{TABLE}.json"

    def check_home(self):
        for t in (TABLE,) + READ_TABLES:
            if not (self.config_dir / f"{t}.json").is_file():
                raise FileNotFoundError(f"Could not find {self.config_dir / (t + '.json')}. "
                                        "Pick the RO3Emu folder that contains data\\config.")

    def load_state(self):
        return read_json(self.state_file, {}) or {}

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def ensure_original(self):
        st = self.load_state()
        if not self.original.is_file() or not st.get("original_sha"):
            self.original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.live, self.original)
            st = {"original_sha": sha256(self.original), "applied_sha": None}
            self.save_state(st)
        return st

    def file_status(self):
        st = self.ensure_original()
        cur = sha256(self.live)
        if cur == st.get("original_sha"):
            return "pristine"
        if cur == st.get("applied_sha"):
            return "applied"
        return "external"

    def rebaseline(self):
        with LOCK:
            shutil.copy2(self.live, self.original)
            self.save_state({"original_sha": sha256(self.original), "applied_sha": None})
            self.cache = self.base = None

    # ------------------------------------------------------------ overrides
    def load_overrides(self):
        d = read_json(OVERRIDES, {}) or {}
        rows = d.get(TABLE, {}) if isinstance(d, dict) else {}
        return {TABLE: rows if isinstance(rows, dict) else {}}

    def save_overrides(self, ov):
        doc = {"_about": "Edits made with the config editor (Shops). Only changed fields are stored. "
                         "Rows use the ShopCommodityConfig row id; _kItemPerPrice is [currency item id, list price].",
               TABLE: {k: ov[TABLE][k] for k in sorted(ov[TABLE], key=int) if ov[TABLE][k]}}
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def count(ov):
        return len(ov[TABLE])

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ data for the page
    def load_game_data(self):
        self.ensure_original()
        base = _index(read_json(self.original))
        self.base = base
        loc = read_json(self.config_dir / "Localization_en.json", {}) or {}
        items = _index(read_json(self.config_dir / "ItemConfig.json", {}))
        looks = _index(read_json(self.config_dir / "WardRobeConfig.json", {}))
        shops = _index(read_json(self.config_dir / "ShopConfig.json", {}))
        by_class = {}
        for s in shops.values():
            by_class.setdefault(int(s.get("_iClassID", 0)), s)

        def item_name(iid):
            r = items.get(str(iid))
            return _text(loc, r.get("_iName")) if r else ""

        def look_name(lid):
            r = looks.get(str(lid))
            return _text(loc, r.get("_iName")) if r else ""

        rows_by_class = {}
        used_currencies = set()
        for rid, r in sorted(base.items(), key=lambda kv: int(kv[0])):
            pair = r.get(K_PRICE) if isinstance(r.get(K_PRICE), list) else []
            cur, price = (pair + [0, 0])[:2] if len(pair) >= 2 else (0, 0)
            look = int(r.get("_iAppearanceId", 0) or 0)
            iid = int(r.get(K_ITEM, 0) or 0)
            used_currencies.add(cur)
            rows_by_class.setdefault(int(r.get("_iClassID", 0)), []).append({
                "id": int(rid), "itemId": iid, "look": look,
                "name": look_name(look) if look else item_name(iid),
                "num": int(r.get(K_NUM, 0) or 0), "per": int(r.get(K_PER, 0) or 0),
                "cur": cur, "price": price, "disc": int(r.get("_iDiscount", 0) or 0),
                "locked": bool(r.get("_iIsLock")) or bool(r.get("_iIsBuy")),
            })
        groups, shown_classes = [], set()
        for gname, classes in GROUPS:
            cl = []
            for c in classes:
                s = by_class.get(c)
                if s is None:
                    continue
                shown_classes.add(c)
                disp = [x for x in (s.get("_kCurrencyDisplay") or []) if isinstance(x, int)]
                used_currencies.update(disp)
                cl.append({"id": c, "name": _text(loc, s.get("_iShopName")) or f"Shop {c}",
                           "hint": FASHION_HINTS.get(c, "") if gname == "Fashion" else "",
                           "display": disp, "rows": rows_by_class.get(c, [])})
            groups.append({"name": gname, "classes": cl})
        currencies = [{"id": c, "name": item_name(c) or f"#{c}"} for c in sorted(used_currencies) if c]
        return {"groups": groups, "currencies": currencies, "periods": {str(k): v for k, v in PERIODS.items()},
                "maxLimit": MAX_LIMIT, "maxPrice": MAX_PRICE}

    def payload(self):
        with LOCK:
            self.check_home()
            self.ensure_original()
            if self.cache is None:
                self.cache = self.load_game_data()
            data = dict(self.cache)
            data["overrides"] = self.load_overrides()
            data["status"] = self.meta()
            return data

    def meta(self):
        with LOCK:
            status = self.file_status()
            ov = self.load_overrides()
            st = self.load_state()
            return {"home": str(self.home), "fileStatus": status,
                    "inSync": status == "applied" and st.get("applied_overrides") == self.ov_hash(ov),
                    "serverRunning": server_running(), "changedRows": self.count(ov),
                    "overridesFile": str(OVERRIDES)}

    # ------------------------------------------------------------ editing
    def _known_item(self, iid):
        items = _index(read_json(self.config_dir / "ItemConfig.json", {}))
        return str(iid) in items

    def _currency_ok(self, cur):
        if self._known_item(cur):
            return True
        # ids the game itself already uses as a currency (Kafra points, for example, are not items)
        return any(isinstance(r.get(K_PRICE), list) and r[K_PRICE][:1] == [cur] for r in self.base.values())

    def _edit(self, ov, row, field, value):
        base = self.base
        if row not in base:
            raise ValueError(f"{TABLE} row {row} does not exist.")
        if field not in FIELD_KEYS:
            raise ValueError(f"{field} cannot be edited here.")
        orig = base[row]
        entry = ov[TABLE].setdefault(row, {})
        key = FIELD_KEYS[field]
        if field == "num":
            if value is not None:
                _int(value, "Purchase limit")
                if not 0 <= value <= MAX_LIMIT:
                    raise ValueError(f"Purchase limit must be between 0 and {MAX_LIMIT} (0 = no limit).")
            new = orig.get(key) if value is None else value
        elif field == "per":
            if value is not None:
                _int(value, "Limit period")
                if value not in PERIODS and value != orig.get(key):
                    raise ValueError("Limit period must be 0 (none), 1 daily, 2 weekly, 3 monthly, 4 lifetime or 5 season.")
            new = orig.get(key) if value is None else value
        elif field in ("cur", "price"):
            base_pair = orig.get(K_PRICE)
            if not isinstance(base_pair, list) or len(base_pair) < 2:
                raise ValueError(f"Row {row} has no [currency, price] pair to edit.")
            pair = list(entry.get(K_PRICE, base_pair))
            idx = 0 if field == "cur" else 1
            if value is None:
                pair[idx] = base_pair[idx]
            else:
                _int(value, "Currency" if idx == 0 else "Price")
                if idx == 0 and not self._currency_ok(value):
                    raise ValueError(f"Currency {value} is not an item in the game.")
                if idx == 1 and not 1 <= value <= MAX_PRICE:
                    raise ValueError(f"Price must be between 1 and {MAX_PRICE:,}.")
                pair[idx] = value
            new = pair[:2] + list(base_pair[2:])
        else:  # itemId
            if int(orig.get("_iAppearanceId", 0) or 0) or int(orig.get("_iAppranceId", 0) or 0):
                raise ValueError("Fashion look rows cannot change what they sell yet (their look is set by several fields).")
            if value is not None:
                _int(value, "Item ID")
                if not self._known_item(value):
                    raise ValueError(f"Item {value} does not exist in ItemConfig.")
            new = orig.get(key) if value is None else value
        if new == orig.get(key):
            entry.pop(key, None)
        else:
            entry[key] = new
        if not entry:
            ov[TABLE].pop(row, None)

    def set_many(self, items):
        """All-or-nothing: every edit is checked first; the overrides file is written once."""
        with LOCK:
            self.check_home()
            self.ensure_original()
            if self.cache is None:
                self.cache = self.load_game_data()
            ov = self.load_overrides()
            for it in items:
                self._edit(ov, str(it["row"]), it["field"], it.get("value"))
            self.save_overrides(ov)
            return {"meta": self.meta(), "overrides": ov}

    def reset_all(self):
        with LOCK:
            self.save_overrides({TABLE: {}})
            return self.meta()

    # ------------------------------------------------------------ apply / restore
    def _backup(self, stamp):
        self.backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.live, self.backups / f"{TABLE}-{stamp}.json")
        for old in sorted(self.backups.glob(f"{TABLE}-*.json"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    OVERLAY_FIELDS = (K_NUM, K_PER, K_PRICE)

    def write_overlay(self, ov, only=None):
        """Lines for the client hook (ConfigOverlay) so the shop window shows the edited price and limit. Only this
        page's own table is replaced in client-overlay/config-overlay.tsv; the card lines of the Items page stay."""
        lines = []
        for rid in sorted(ov[TABLE], key=int):
            if only is not None and rid not in only:
                continue
            flds = ov[TABLE][rid]
            for f in self.OVERLAY_FIELDS:
                if f not in flds:
                    continue
                v = flds[f]
                text = f"{v[0]}:{v[1]}" if isinstance(v, list) else str(v)
                lines.append("\t".join([TABLE, rid, f, text]))
        return update_overlay({TABLE}, lines)

    def apply(self):
        with LOCK:
            self.check_home()
            st = self.ensure_original()
            if self.file_status() == "external":
                raise RuntimeError(f"{TABLE}.json was changed outside this tool. "
                                   "Use 'Use current file as new original' first.")
            ov = self.load_overrides()
            data = read_json(self.original)
            idx = _index(data)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            fields, skipped = 0, []
            for rid, flds in ov[TABLE].items():
                if rid not in idx:
                    skipped.append(rid)
                    continue
                for f, v in flds.items():
                    idx[rid][f] = list(v) if isinstance(v, list) else v
                    fields += 1
            self._backup(stamp)
            atomic_write(self.live, compact(data) + "\n")
            st["applied_sha"] = sha256(self.live)
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            overlay = self.write_overlay(ov, only=set(idx) & set(ov[TABLE]))
            return {"rows": self.count(ov) - len(skipped), "fields": fields, "skippedRows": skipped,
                    "backup": stamp, "overlay": overlay, "published": dict(common.LAST_PUBLISH), "meta": self.meta()}

    def restore_original(self):
        with LOCK:
            self.check_home()
            st = self.ensure_original()
            self._backup(datetime.now().strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(self.original, self.live)
            st["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            update_overlay({TABLE}, [])      # originals are back: nothing for the client to override
            return self.meta()

    # ------------------------------------------------------------ csv
    HEADER = ["Tab", "Shop", "ClassID", "CommodityID", "Kind", "ItemOrLookID", "EnglishName", "PurchaseLimit",
              "LimitPeriod", "LimitPeriodName", "CurrencyID", "CurrencyName", "Price"]

    def effective(self, row, ov=None):
        """The values of a row with the saved edits applied: dict num, per, cur, price, itemId."""
        ov = ov or self.load_overrides()
        r = self.base[str(row)]
        e = ov[TABLE].get(str(row), {})
        pair = e.get(K_PRICE, r.get(K_PRICE) or [0, 0])
        return {"num": e.get(K_NUM, r.get(K_NUM, 0)), "per": e.get(K_PER, r.get(K_PER, 0)),
                "cur": pair[0], "price": pair[1], "itemId": e.get(K_ITEM, r.get(K_ITEM, 0))}

    def export_csv(self):
        with LOCK:
            self.check_home()
            if self.cache is None:
                self.cache = self.load_game_data()
            ov = self.load_overrides()
            names = {c["id"]: c["name"] for c in self.cache["currencies"]}
            out = io.StringIO()
            w = csv.writer(out, lineterminator="\r\n")
            w.writerow(self.HEADER)
            for g in self.cache["groups"]:
                for c in g["classes"]:
                    for r in c["rows"]:
                        e = self.effective(r["id"], ov)
                        look = bool(r["look"])
                        w.writerow([g["name"], c["name"], c["id"], r["id"], "look" if look else "item",
                                    r["look"] if look else e["itemId"], r["name"], e["num"], e["per"],
                                    PERIODS.get(e["per"], str(e["per"])), e["cur"], names.get(e["cur"], ""), e["price"]])
            return "﻿" + out.getvalue()

    @staticmethod
    def _norm(name):
        return "".join(ch for ch in (name or "").lower() if ch.isalnum())

    def _parse_csv(self, text):
        """Returns (items, changes, errors, unchanged). items are the set_many edits that would be applied."""
        if self.cache is None:
            self.cache = self.load_game_data()
        ov = self.load_overrides()
        reader = csv.reader(io.StringIO((text or "").lstrip("﻿")))
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError("The file is empty.")
        cols = {self._norm(h): i for i, h in enumerate(header)}
        aliases = {"commodityid": ("commodityid", "id"), "itemorlookid": ("itemorlookid", "itemid"),
                   "purchaselimit": ("purchaselimit", "limit"), "limitperiod": ("limitperiod", "period"),
                   "currencyid": ("currencyid", "currency"), "price": ("price",), "classid": ("classid",),
                   "kind": ("kind",)}
        pos = {k: next((cols[a] for a in names if a in cols), None) for k, names in aliases.items()}
        if pos["commodityid"] is None:
            raise ValueError("The file needs a CommodityID column (export the CSV first to see the layout).")
        items, changes, errors, unchanged = [], [], [], 0
        classes = {r["id"]: c["id"] for g in self.cache["groups"] for c in g["classes"] for r in c["rows"]}
        names = {r["id"]: r["name"] for g in self.cache["groups"] for c in g["classes"] for r in c["rows"]}
        for n, rec in enumerate(reader, start=2):
            if not rec or not any(x.strip() for x in rec):
                continue
            get = lambda k: (rec[pos[k]].strip() if pos[k] is not None and pos[k] < len(rec) else "")
            try:
                cid = int(get("commodityid"))
            except ValueError:
                errors.append(f"Line {n}: CommodityID '{get('commodityid')}' is not a number.")
                continue
            if cid not in classes:
                errors.append(f"Line {n}: commodity {cid} is not in the Fashion, Shop or Point Mall tabs.")
                continue
            if get("classid") and get("classid") != str(classes[cid]):
                errors.append(f"Line {n}: commodity {cid} belongs to shop class {classes[cid]}, not {get('classid')}.")
                continue
            cur_vals = self.effective(cid, ov)
            row = next(r for g in self.cache["groups"] for c in g["classes"] for r in c["rows"] if r["id"] == cid)
            want = {}
            try:
                if get("purchaselimit"):
                    want["num"] = _whole(get("purchaselimit"), "PurchaseLimit")
                if get("limitperiod"):
                    p = get("limitperiod").lower()
                    want["per"] = PERIOD_BY_NAME[p] if p in PERIOD_BY_NAME else _whole(p, "LimitPeriod")
                if get("currencyid"):
                    want["cur"] = _whole(get("currencyid"), "CurrencyID")
                if get("price"):
                    want["price"] = _whole(get("price"), "Price")
                if get("itemorlookid"):
                    v = _whole(get("itemorlookid"), "ItemOrLookID")
                    if row["look"]:
                        if v != row["look"]:
                            raise ValueError("this is a fashion look row; its look cannot be changed here")
                    else:
                        want["itemId"] = v
            except ValueError as exc:
                errors.append(f"Line {n} (commodity {cid}): {exc}")
                continue
            changed_here = False
            for f, v in want.items():
                if v != cur_vals[f]:
                    items.append({"row": cid, "field": f, "value": v})
                    changes.append({"row": cid, "name": names.get(cid, ""), "field": f, "old": cur_vals[f], "new": v})
                    changed_here = True
            if not changed_here:
                unchanged += 1
        # dry run through the real checks so the preview shows the same errors an import would hit
        probe = self.load_overrides()
        for it in list(items):
            try:
                self._edit(probe, str(it["row"]), it["field"], it["value"])
            except ValueError as exc:
                errors.append(f"Commodity {it['row']}, {it['field']}: {exc}")
        return items, changes, errors, unchanged

    def import_preview(self, text):
        with LOCK:
            self.check_home()
            _, changes, errors, unchanged = self._parse_csv(text)
            return {"changes": changes, "errors": errors, "unchanged": unchanged}

    def import_apply(self, text):
        with LOCK:
            self.check_home()
            items, changes, errors, _ = self._parse_csv(text)
            if errors:
                raise ValueError("Nothing was imported. " + " | ".join(errors[:5]) + (" ..." if len(errors) > 5 else ""))
            if items:
                self.set_many(items)
            return {"imported": len(changes), "meta": self.meta()}
