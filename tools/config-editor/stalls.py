"""Stall price editing for the RO3 Plus Config Editor (page "Stall prices").

Which items can be sold on a player stall, and the lowest and highest price a player may ask, come from one table:
data/config/TradeConfig.json (one row per item id). What the server does with a row (StallsService.cs, CONFIRMED):
  * SaleConfig: an item can go on a stall when its TradeConfig row has _iTrade == 2 (and is in season). Rows with
    _iTrade == 1 belong to the other market (TradeService) and are not touched by this page.
  * Range / ValidPrice: _kStableRange = [min, max] in tenths of _iBasePrice (5 = 50 %, 15 = 150 %). A player's price must
    equal floor((_iBasePrice * step + 5) / 10) for a whole step between min and max, so prices move in 10 % of base steps.
    min is clamped to 1..1000 and max to min..1000 (1000 = 100 times the base price), and no price may pass the public
    parameter TradeLimitPrice (999,999,999 when missing). _kVariableRange is not read by the server; this page keeps it
    equal to _kStableRange, like the original rows.
"Unlimited max" writes the highest max the server accepts (1000 = 100 times the base price). A truly free price would need
a server change; this page does not make one.
  * ShelveLimit is how many of an item can go into one stall slot (rows keep _iBuyLimit equal to it).

Your edits live in overrides/stalls.json (only what you changed):
  Edits  {item id: {"min": %, "max": %, "unlimited": bool, "base": price}}  changed fields of existing stall rows
  Added  {item id: {"currency", "base", "min", "max", "unlimited", "limit"}}  stall rows this page creates
"Apply" rewrites TradeConfig.json = untouched original + your edits, with a backup first. The server reads the table at
start, so restart it.
The game client keeps its own copy of TradeConfig and hides the Stall button for an item without a row there (the bag says
"Untradeable"; CONFIRMED in game with item 1011201). So Apply also writes lines for the client hook (ConfigOverlay) into
client-overlay/config-overlay.tsv and copies the file into the client: "TradeConfig <id> @clone <source row>" plus the row's
fields for every added item, and the changed fields of edited rows. Removing an added item and applying drops its lines, and
the item is "Untradeable" again after the client restarts. The hook needs the rebuilt client hooks (see README).
Standard library only.
"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

import common
from common import APP_DIR, LOCK, STATE_DIR, atomic_write, compact, read_json, server_running, sha256, update_overlay

OVERRIDES = APP_DIR / "overrides" / "stalls.json"
TABLE = "TradeConfig"
STALL = 2                    # TradeConfig._iTrade value of a stall row
UNLIMITED_TENTHS = 1000      # the highest max the server accepts: 100 times the base price
MAX_BACKUPS = 5
DEFAULT_PRICE_CEILING = 999_999_999
CURRENCIES = {1: "Zeny", 2: "Minted Coin"}
# Fields the client hook can set on a TradeConfig row (numbers, and [min, max] pairs written "5:15").
OVERLAY_NUMBERS = ("_iTrade", "_iType", "_iSubType", "_iBasePrice", "_iMoney", "_iSort", "_iShelveLimit", "_iBuyLimit")
OVERLAY_PAIRS = ("_kStableRange", "_kVariableRange")
OVERLAY_EDITED = ("_iBasePrice", "_kStableRange", "_kVariableRange")


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _text(loc, ref):
    tid = ref[0] if isinstance(ref, list) and ref else ref
    entry = loc.get(str(tid)) if isinstance(tid, int) and tid else None
    return entry.get("_kDes", "") if isinstance(entry, dict) else ""


def _whole(value, what, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise ValueError(f"{what} must be a whole number.")
    value = int(value)
    if value < lo or value > hi:
        raise ValueError(f"{what} must be between {lo:,} and {hi:,}.")
    return value


def price_at(base, tenths):
    """The price of one step (the server's own rule: floor((base * step + 5) / 10))."""
    return (base * tenths + 5) // 10


class StallsEditor:
    def __init__(self, home: Path):
        self.home = home
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.originals = self.state_dir / "stalls_originals"
        self.backups = self.state_dir / "stalls_backups"
        self.state_file = self.state_dir / "stalls_state.json"
        self.orig = None          # id -> row of the untouched TradeConfig

    # ------------------------------------------------------------ paths and state
    @property
    def config_dir(self):
        return self.home / "data" / "config"

    @property
    def live(self):
        return self.config_dir / f"{TABLE}.json"

    @property
    def original(self):
        return self.originals / f"{TABLE}.json"

    def check_home(self):
        if not self.live.is_file():
            raise FileNotFoundError(f"Could not find {self.live}. Pick the RO3Emu folder that contains data\\config.")

    def load_state(self):
        st = read_json(self.state_file, {}) or {}
        st.setdefault("original_sha", None)
        st.setdefault("applied_sha", None)
        return st

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def ensure_original(self):
        """Keep an untouched copy of TradeConfig before this page first changes it (taken from the live file only while
        the live file is still the original, never from a file this page wrote)."""
        st = self.load_state()
        if st["original_sha"] and self.original.is_file():
            return st
        if st["applied_sha"]:
            raise RuntimeError("The saved original TradeConfig is missing. Use 'Use current file as new original' "
                               "(the file is the one this page wrote).")
        self.originals.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.live, self.original)
        st["original_sha"] = sha256(self.original)
        st["applied_sha"] = None
        self.save_state(st)
        return st

    def file_status(self):
        """pristine: never changed by us. applied: holds what Apply wrote. external: edited somewhere else."""
        st = self.load_state()
        if not self.live.is_file():
            return "external"
        cur = sha256(self.live)
        if st["applied_sha"]:
            return "applied" if cur == st["applied_sha"] else "external"
        if st["original_sha"] and cur != st["original_sha"]:
            return "external"
        return "pristine"

    def rebaseline(self):
        with LOCK:
            shutil.rmtree(self.originals, ignore_errors=True)
            self.save_state({"original_sha": None, "applied_sha": None, "applied_overrides": None})
            self.orig = None

    # ------------------------------------------------------------ overrides
    def load_overrides(self):
        d = read_json(OVERRIDES, {}) or {}
        edits = d.get("Edits") if isinstance(d, dict) else None
        added = d.get("Added") if isinstance(d, dict) else None
        return {"Edits": edits if isinstance(edits, dict) else {}, "Added": added if isinstance(added, dict) else {}}

    def save_overrides(self, ov):
        doc = {"_about": "Edits made with the config editor (Stall prices). Edits: item id > changed fields (min %, max %, unlimited, "
                         "base price). Added: item id > a new stall row (currency, base, min, max, unlimited, limit). "
                         "Missing = original TradeConfig.",
               "Edits": {k: ov["Edits"][k] for k in sorted(ov["Edits"], key=int)},
               "Added": {k: ov["Added"][k] for k in sorted(ov["Added"], key=int)}}
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def count(ov):
        return len(ov["Edits"]) + len(ov["Added"])

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ original data
    def _load(self):
        self.check_home()
        self.ensure_original()
        if self.orig is None:
            data = read_json(self.original, {}) or {}
            if not isinstance(data, dict):
                raise ValueError("TradeConfig does not look like the expected table.")
            self.orig = {k: v for k, v in data.items() if isinstance(v, dict)}

    def _game(self):
        """Names, item rows and the price ceiling, read fresh (these tables are not edited here)."""
        loc = read_json(self.config_dir / "Localization_en.json", {}) or {}
        items = read_json(self.config_dir / "ItemConfig.json", {}) or {}
        params = read_json(self.config_dir / "PublicParamConfig.json", {}) or {}
        ceiling = DEFAULT_PRICE_CEILING
        if isinstance(params, dict):
            p = params.get("TradeLimitPrice")
            if isinstance(p, dict):
                p = p.get("_iValue", p.get("_kValue"))
            if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0:
                ceiling = int(p)
        return loc, items, ceiling

    @staticmethod
    def _range_of(row):
        r = row.get("_kStableRange")
        if isinstance(r, list) and len(r) >= 2:
            lo = max(1, min(UNLIMITED_TENTHS, int(_num(r[0], 10))))
            hi = max(lo, min(UNLIMITED_TENTHS, int(_num(r[1], 10))))
            return lo, hi
        return 10, 10

    def _stall_rows(self):
        return {k: v for k, v in self.orig.items() if int(_num(v.get("_iTrade"))) == STALL}

    # ------------------------------------------------------------ reading for the page
    def _view(self, iid, row_vals, name, ceiling, origin):
        """One line of the page from effective values {currency, base, minT, maxT, limit}."""
        base, lo, hi = row_vals["base"], row_vals["minT"], row_vals["maxT"]
        lo_p, hi_p = price_at(base, lo), min(price_at(base, hi), ceiling)
        return {"id": int(iid), "name": name, "currency": row_vals["currency"], "base": base,
                "min": lo * 10, "max": hi * 10, "unlimited": hi >= UNLIMITED_TENTHS,
                "minPrice": lo_p, "maxPrice": hi_p, "limit": row_vals["limit"], **origin}

    def get(self):
        with LOCK:
            self._load()
            ov = self.load_overrides()
            loc, items, ceiling = self._game()
            rows = []
            for iid, row in sorted(self._stall_rows().items(), key=lambda kv: int(kv[0])):
                lo, hi = self._range_of(row)
                orig_vals = {"currency": int(_num(row.get("_iMoney"), 1)), "base": int(_num(row.get("_iBasePrice"))),
                             "minT": lo, "maxT": hi, "limit": int(_num(row.get("_iShelveLimit")))}
                name = _text(loc, (items.get(iid) or {}).get("_iName")) or str(row.get("_kSpuName", ""))
                vals = dict(orig_vals)
                e = ov["Edits"].get(iid)
                if e:
                    if "base" in e:
                        vals["base"] = e["base"]
                    if "min" in e:
                        vals["minT"] = e["min"] // 10
                    if "unlimited" in e or "max" in e:
                        vals["maxT"] = UNLIMITED_TENTHS if e.get("unlimited") else e.get("max", hi * 10) // 10
                    if vals["maxT"] < vals["minT"]:
                        vals["maxT"] = vals["minT"]
                origin = {"added": False, "edited": bool(e), "orig": {"min": lo * 10, "max": hi * 10,
                          "unlimited": hi >= UNLIMITED_TENTHS, "base": orig_vals["base"]}}
                rows.append(self._view(iid, vals, name, ceiling, origin))
            for iid, a in sorted(ov["Added"].items(), key=lambda kv: int(kv[0])):
                vals = {"currency": a["currency"], "base": a["base"], "minT": a["min"] // 10,
                        "maxT": UNLIMITED_TENTHS if a.get("unlimited") else a["max"] // 10, "limit": a["limit"]}
                name = _text(loc, (items.get(iid) or {}).get("_iName"))
                rows.append(self._view(iid, vals, name or "?", ceiling, {"added": True, "edited": True, "orig": None}))
            return {"rows": rows, "ceiling": ceiling, "currencies": {str(k): v for k, v in CURRENCIES.items()},
                    "unlimitedPercent": UNLIMITED_TENTHS * 10, "meta": self.meta()}

    def meta(self):
        with LOCK:
            ov = self.load_overrides()
            st = self.load_state()
            status = self.file_status()
            nothing = not self.count(ov) and status == "pristine"
            return {"home": str(self.home), "fileStatus": status,
                    "inSync": status != "external" and (nothing or st.get("applied_overrides") == self.ov_hash(ov)),
                    "serverRunning": server_running(), "changed": self.count(ov), "overridesFile": str(OVERRIDES)}

    # ------------------------------------------------------------ validation
    def _clean(self, vals, ceiling, what, need_limit=False):
        base = _whole(vals.get("base"), f"{what}: base price", 1, ceiling)
        unlimited = bool(vals.get("unlimited"))
        lo = _whole(vals.get("min"), f"{what}: min %", 10, UNLIMITED_TENTHS * 10)
        hi = UNLIMITED_TENTHS * 10 if unlimited else _whole(vals.get("max"), f"{what}: max %", 10, UNLIMITED_TENTHS * 10)
        if lo % 10 or hi % 10:
            raise ValueError(f"{what}: min % and max % must be multiples of 10 (the server prices in steps of 10 % of the base price).")
        if lo > hi:
            raise ValueError(f"{what}: min % ({lo}) is higher than max % ({hi}).")
        if hi >= UNLIMITED_TENTHS * 10:
            unlimited = True
        out = {"base": base, "min": lo, "max": hi, "unlimited": unlimited}
        if need_limit:
            out["limit"] = _whole(vals.get("limit"), f"{what}: limit per slot", 1, 99_999)
        return out

    # ------------------------------------------------------------ editing
    def set_many(self, items):
        """items = [{id, base, min, max, unlimited[, limit]}] full values of rows the page changed."""
        if not isinstance(items, list) or not items:
            raise ValueError("Nothing to save.")
        with LOCK:
            self._load()
            _, _, ceiling = self._game()
            ov = self.load_overrides()
            stall = self._stall_rows()
            for it in items:
                iid = str(it.get("id"))
                if iid in ov["Added"]:
                    cur = ov["Added"][iid]
                    new = self._clean({**it, "limit": it.get("limit", cur["limit"])}, ceiling, f"Item {iid}", need_limit=True)
                    ov["Added"][iid] = {**cur, **new}
                    continue
                if iid not in stall:
                    raise ValueError(f"Item {iid} is not a stall item.")
                new = self._clean(it, ceiling, f"Item {iid}")
                row = stall[iid]
                lo, hi = self._range_of(row)
                diff = {}
                if new["base"] != int(_num(row.get("_iBasePrice"))):
                    diff["base"] = new["base"]
                if new["min"] != lo * 10:
                    diff["min"] = new["min"]
                if new["unlimited"] != (hi >= UNLIMITED_TENTHS) or (not new["unlimited"] and new["max"] != hi * 10):
                    if new["unlimited"]:
                        diff["unlimited"] = True
                    else:
                        diff["max"] = new["max"]
                        diff["unlimited"] = False
                if diff:
                    ov["Edits"][iid] = diff
                else:
                    ov["Edits"].pop(iid, None)
            self.save_overrides(ov)
            return self.get()

    def item_info(self, item_id):
        with LOCK:
            self._load()
            loc, items, _ = self._game()
            iid = str(_whole(item_id, "Item id", 1, 2_000_000_000))
            r = items.get(iid)
            if not isinstance(r, dict):
                return {"known": False, "name": "", "note": "No such item in ItemConfig."}
            note = ""
            row = self.orig.get(iid)
            if row is not None and int(_num(row.get("_iTrade"))) == STALL:
                note = "Already on the stall list."
            elif row is not None:
                note = "This item already has a row for the other market (not a stall row). It can't be added here."
            elif int(_num(r.get("_iTrade"))) == 0:
                note = "ItemConfig marks this item untradeable. The server accepts the row, but the client may still hide Stall."
            return {"known": True, "name": _text(loc, r.get("_iName")), "note": note,
                    "stack": int(_num(r.get("_iStackLimit"))), "itemTrade": int(_num(r.get("_iTrade")))}

    def add_item(self, item_id, currency, base, minp, maxp, unlimited, limit):
        with LOCK:
            self._load()
            loc, items, ceiling = self._game()
            iid = str(_whole(item_id, "Item id", 1, 2_000_000_000))
            if not isinstance(items.get(iid), dict):
                raise ValueError(f"Item {iid} does not exist in ItemConfig.")
            if iid in self.orig:
                raise ValueError(f"Item {iid} already has a TradeConfig row"
                                 + (" (it is already on the stall list)." if iid in self._stall_rows()
                                    else " for the other market. Stall and market share that row, so it can't be added here."))
            currency = _whole(currency, "Currency", 1, 2)
            new = self._clean({"base": base, "min": minp, "max": maxp, "unlimited": unlimited, "limit": limit}, ceiling,
                              f"Item {iid}", need_limit=True)
            ov = self.load_overrides()
            if iid in ov["Added"]:
                raise ValueError(f"Item {iid} is already added. Edit it in the list.")
            ov["Added"][iid] = {"currency": currency, **new}
            self.save_overrides(ov)
            return self.get()

    def remove_added(self, item_id):
        with LOCK:
            self._load()
            ov = self.load_overrides()
            if ov["Added"].pop(str(item_id), None) is None:
                raise ValueError("That item is not one you added.")
            self.save_overrides(ov)
            return self.get()

    def reset_all(self):
        with LOCK:
            self._load()
            self.save_overrides({"Edits": {}, "Added": {}})
            return self.get()

    # ------------------------------------------------------------ building the table
    def _template(self, currency):
        """The stall row a new row is copied from: a plain one (no premium-card, scale-down, season or reduce settings)
        with the same currency, else any plain one."""
        def plain(r):
            return (int(_num(r.get("_iPremiumCard"))) == 0 and int(_num(r.get("_iScaleDown"))) == 0
                    and not r.get("_kGameSeason") and not r.get("_kReduceRange") and not r.get("_iReduceTigger"))
        rows = [r for r in self._stall_rows().values() if plain(r)]
        same = [r for r in rows if int(_num(r.get("_iMoney"))) == currency]
        pool = same or rows or list(self._stall_rows().values())
        if not pool:
            raise RuntimeError("TradeConfig has no stall row to copy from.")
        return pool[0]

    def build(self, ov):
        loc, items, _ = self._game()
        out = {}
        for iid, row in self.orig.items():
            e = ov["Edits"].get(iid)
            if not e:
                out[iid] = row
                continue
            n = dict(row)
            lo, hi = self._range_of(row)
            if "base" in e:
                n["_iBasePrice"] = e["base"]
            if "min" in e:
                lo = e["min"] // 10
            if "unlimited" in e or "max" in e:
                hi = UNLIMITED_TENTHS if e.get("unlimited") else e.get("max", hi * 10) // 10
            hi = max(lo, hi)
            n["_kStableRange"] = [lo, hi]
            n["_kVariableRange"] = [lo, hi]
            out[iid] = n
        top = max([int(_num(r.get("_iSort"))) for r in self.orig.values()] or [0])
        for iid, a in sorted(ov["Added"].items(), key=lambda kv: int(kv[0])):
            if iid in out:
                raise RuntimeError(f"Item {iid} now has a TradeConfig row of its own. Remove it from your added items.")
            item = items.get(iid)
            if not isinstance(item, dict):
                raise RuntimeError(f"Item {iid} no longer exists in ItemConfig.")
            n = json.loads(json.dumps(self._template(a["currency"])))
            top += 1
            lo = a["min"] // 10
            hi = UNLIMITED_TENTHS if a.get("unlimited") else a["max"] // 10
            n.update({"_iID": int(iid), "_iBasePrice": a["base"], "_iMoney": a["currency"], "_iSort": top,
                      "_iType": int(_num(item.get("_iType"))), "_iSubType": int(_num(item.get("_iSubType"))),
                      "_kSpuName": _text(loc, item.get("_iName")) or iid, "_iTrade": STALL,
                      "_iShelveLimit": a["limit"], "_iBuyLimit": a["limit"],
                      "_kStableRange": [lo, hi], "_kVariableRange": [lo, hi]})
            out[iid] = n
        return out

    # ------------------------------------------------------------ client overlay
    def write_overlay(self, ov, out):
        """Lines for the client hook (ConfigOverlay): an added item is a copy of the stall row it was built from plus its
        own fields; an edited row only gets its changed fields. Only this page's table is replaced in
        client-overlay/config-overlay.tsv (the Items and Shops pages keep their lines), and the file is copied into the client."""
        def line(iid, field, value):
            text = f"{value[0]}:{value[1]}" if isinstance(value, list) else str(int(value))
            return "\t".join([TABLE, iid, field, text])

        lines = []
        for iid in sorted(ov["Added"], key=int):
            row = out[iid]
            lines.append("\t".join([TABLE, iid, "@clone", str(int(self._template(ov["Added"][iid]["currency"])["_iID"]))]))
            lines += [line(iid, f, row[f]) for f in OVERLAY_NUMBERS + OVERLAY_PAIRS]
        for iid in sorted(ov["Edits"], key=int):
            if iid in self.orig and iid in out:
                lines += [line(iid, f, out[iid][f]) for f in OVERLAY_EDITED if f in out[iid]]
        return update_overlay({TABLE}, lines)

    # ------------------------------------------------------------ apply
    def _backup(self, stamp):
        self.backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.live, self.backups / f"{TABLE}-{stamp}.json")
        for old in sorted(self.backups.glob(f"{TABLE}-*.json"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    def apply(self):
        with LOCK:
            self._load()
            if self.file_status() == "external":
                raise RuntimeError("TradeConfig was changed outside this tool. Use 'Use current file as new original' first.")
            ov = self.load_overrides()
            st = self.load_state()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            if self.count(ov):
                built = self.build(ov)
                text = compact(built)
                self._backup(stamp)
                atomic_write(self.live, text)
                st["applied_sha"] = sha256(self.live)
                overlay = self.write_overlay(ov, built)
            else:
                if st["applied_sha"]:                # no edits left: put the original back
                    self._backup(stamp)
                    shutil.copy2(self.original, self.live)
                    st["applied_sha"] = None
                overlay = update_overlay({TABLE}, [])  # and nothing left for the client either
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            return {"rows": self.count(ov), "backup": stamp, "overlay": overlay,
                    "published": dict(common.LAST_PUBLISH), "meta": self.meta()}

    def restore_original(self):
        with LOCK:
            self._load()
            if self.file_status() == "external":
                raise RuntimeError("TradeConfig was changed outside this tool. Use 'Use current file as new original' first.")
            st = self.load_state()
            if st["applied_sha"] and self.original.is_file():
                self._backup(datetime.now().strftime("%Y%m%d-%H%M%S"))
                shutil.copy2(self.original, self.live)
                st["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            update_overlay({TABLE}, [])              # originals are back: nothing for the client to override
            return self.meta()
