"""Level penalty editing for the RO3 Plus Config Editor (page "Level penalty").

Two tables decide how much a kill is worth when the player and the monster are far apart in level:
  * EXP   data/config/LvExpCorrectionConfig.json  401 rows, one per level gap -200..200. _iLvDifferenceCorrection is the
          share of the Base EXP per 10000 (RewardService.ResolveKill, CONFIRMED: gap = player level - monster level,
          baseExp * correction / 10000, a gap with no row counts as 10000). Job EXP follows Base EXP.
  * Drop  data/config/DropAttenuationConfig.json  rows of plans (_iPlanID). A drop row that names a plan has its count scaled
          by the plan's _iAttenuationRatio per 10000 for the same gap (DropAttenuation.cs, CONFIRMED). Only drop rows that name
          a plan are affected, there is no global drop penalty. A gap no row covers counts as 10000.

This page shows both tables like rAthena's level_penalty: a short list of steps "Difference -> Rate".
  Difference = monster level minus player level (the server's gap is the opposite way round: gap = -Difference).
  Rate       = percent of the normal reward. 100 = full, 0 = nothing, above 100 = bonus (EXP only makes sense above 100).
How a list of steps is read (the same rule rAthena uses, LIKELY for rAthena, CONFIRMED for this page's own expansion):
  * Difference 0 or more (monster stronger): a step holds from its Difference up to the next higher step. Before the first
    step the rate is 100.
  * Difference below 0 (monster weaker): a step holds from its Difference down to the next lower step. Between -1 and the
    first negative step the rate is 100.
  * The last step on each side holds for every level beyond it.
Your edits live in overrides/levelpenalty.json. "Apply" rewrites the config table you edited (untouched original + your
steps, with a backup first). The server reads the tables at start, so restart it. Nothing here reaches the client.
Standard library only.
"""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from common import APP_DIR, LOCK, STATE_DIR, atomic_write, read_json, server_running, sha256

OVERRIDES = APP_DIR / "overrides" / "levelpenalty.json"
MAX_BACKUPS = 5
TABLES = ("LvExpCorrectionConfig", "DropAttenuationConfig")
EXP_RANGE = (-200, 200)       # the Difference span of the EXP table (the server clamps the gap to this)
DROP_RANGE = (-999, 999)      # the span the original drop plans cover
RATE_MAX = 1000.0             # percent
MAX_STEPS = 400


def _num(v, default=0):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _dump(obj) -> str:
    """Compact JSON exactly like the original files (they have no spaces and no final newline)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _first(v):
    return v[0] if isinstance(v, list) and v else v


# ---------------------------------------------------------------- the step rule (pure functions)
def rate_at(steps, d):
    """Percent for Difference d from a list of (difference, rate) steps (rAthena rule, see the module text)."""
    if d >= 0:
        best = None
        for v, r in steps:
            if 0 <= v <= d and (best is None or v > best[0]):
                best = (v, r)
    else:
        best = None
        for v, r in steps:
            if v < 0 and v >= d and (best is None or v < best[0]):
                best = (v, r)
    return 100.0 if best is None else best[1]


def derive_steps(rates):
    """Shortest list of steps that reproduces rates = {Difference: percent} (a contiguous span around 0)."""
    lo, hi = min(rates), max(rates)
    steps = []
    prev = None
    for d in range(0, hi + 1):                    # monster stronger side, walking away from 0
        r = rates.get(d, 100.0)
        if r != prev:
            if not (d == 0 and r == 100.0):
                steps.append((d, r))
            prev = r
    prev = None
    for d in range(-1, lo - 1, -1):               # monster weaker side
        r = rates.get(d, 100.0)
        if r != prev:
            if not (d == -1 and r == 100.0):
                steps.append((d, r))
            prev = r
    return sorted(steps, reverse=True)


def clean_steps(raw, lo, hi):
    """Validate what the page sends: [[difference, rate], ...]. Returns sorted (difference, rate) tuples."""
    if not isinstance(raw, list):
        raise ValueError("Steps must be a list.")
    if len(raw) > MAX_STEPS:
        raise ValueError(f"At most {MAX_STEPS} steps.")
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError("Each step needs a Difference and a Rate.")
        d, r = item
        if isinstance(d, bool) or isinstance(r, bool) or not isinstance(d, (int, float)) or not isinstance(r, (int, float)):
            raise ValueError("Difference and Rate must be numbers.")
        if d != int(d):
            raise ValueError(f"Difference {d} must be a whole number.")
        d = int(d)
        if d < lo or d > hi:
            raise ValueError(f"Difference {d} is outside {lo}..{hi}.")
        if d in seen:
            raise ValueError(f"Difference {d} is listed twice.")
        r = round(float(r), 2)
        if r < 0 or r > RATE_MAX:
            raise ValueError(f"Rate {r} must be between 0 and {RATE_MAX:g} percent.")
        seen.add(d)
        out.append((d, r))
    return sorted(out, reverse=True)


def _pct_to_raw(r):
    return int(round(r * 100))


def _raw_to_pct(v):
    p = v / 100.0
    return int(p) if p == int(p) else round(p, 2)


def _steps_json(steps):
    return [[d, int(r) if r == int(r) else r] for d, r in steps]


class LevelPenaltyEditor:
    def __init__(self, home: Path):
        self.home = home
        tag = hashlib.sha1(str(home).lower().encode("utf-8")).hexdigest()[:8]
        self.state_dir = STATE_DIR / tag
        self.originals = self.state_dir / "levelpenalty_originals"
        self.backups = self.state_dir / "levelpenalty_backups"
        self.state_file = self.state_dir / "levelpenalty_state.json"
        self.orig = None       # {"Exp": rows, "Drop": rows} of the untouched originals

    # ------------------------------------------------------------ paths and state
    @property
    def config_dir(self):
        return self.home / "data" / "config"

    def live(self, table):
        return self.config_dir / f"{table}.json"

    def original(self, table):
        return self.originals / f"{table}.json"

    def check_home(self):
        for t in TABLES:
            if not self.live(t).is_file():
                raise FileNotFoundError(f"Could not find {self.live(t)}. Pick the RO3Emu folder that contains data\\config.")

    def load_state(self):
        st = read_json(self.state_file, {}) or {}
        st.setdefault("tables", {})
        return st

    def save_state(self, st):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(self.state_file, json.dumps(st, indent=2) + "\n")

    def ensure_original(self, table):
        """Keep an untouched copy of a table before this page first changes it. Taken from the live file only while the
        live file is still the original (never from a file this page wrote)."""
        st = self.load_state()
        s = st["tables"].get(table)
        if s and self.original(table).is_file():
            return st
        self.originals.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.live(table), self.original(table))
        st["tables"][table] = {"original_sha": sha256(self.original(table)), "applied_sha": None}
        self.save_state(st)
        return st

    def file_status(self):
        """pristine: no table of ours was ever changed. applied: tables hold what Apply wrote. external: edited elsewhere."""
        st = self.load_state()
        any_applied = False
        for t, s in st["tables"].items():
            p = self.live(t)
            if not p.is_file():
                return "external"
            cur = sha256(p)
            if s.get("applied_sha"):
                any_applied = True
                if cur != s["applied_sha"]:
                    return "external"
            elif cur != s.get("original_sha"):
                return "external"
        return "applied" if any_applied else "pristine"

    def rebaseline(self):
        with LOCK:
            shutil.rmtree(self.originals, ignore_errors=True)
            self.save_state({"tables": {}, "applied_overrides": None})
            self.orig = None

    # ------------------------------------------------------------ overrides
    def load_overrides(self):
        d = read_json(OVERRIDES, {}) or {}
        exp = d.get("Exp") if isinstance(d, dict) else None
        drop = d.get("Drop", {}) if isinstance(d, dict) else {}
        return {"Exp": exp if isinstance(exp, list) else None, "Drop": drop if isinstance(drop, dict) else {}}

    def save_overrides(self, ov):
        doc = {"_about": "Edits made with the config editor (Level penalty). Exp: list of [Difference, Rate %], Difference = monster level "
                         "minus player level. Drop: plan id > list of [Difference, Rate %]. Missing = original table.",
               "Exp": _steps_json(ov["Exp"]) if ov["Exp"] is not None else None,
               "Drop": {p: _steps_json(ov["Drop"][p]) for p in sorted(ov["Drop"], key=int)}}
        if doc["Exp"] is None:
            del doc["Exp"]
        atomic_write(OVERRIDES, json.dumps(doc, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def count(ov):
        return (1 if ov["Exp"] is not None else 0) + len(ov["Drop"])

    @staticmethod
    def ov_hash(ov):
        return hashlib.sha256(json.dumps(ov, sort_keys=True, default=list).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------ original data
    def _load(self):
        self.check_home()
        st = self.load_state()
        for t in TABLES:
            if self.original(t).is_file():
                continue
            if st["tables"].get(t, {}).get("applied_sha"):
                raise RuntimeError(f"The saved original of {t} is missing. Use 'Use current files as new original' (the file is the one this page wrote).")
            self.ensure_original(t)     # nothing of ours wrote this table, so the live file is the original
        if self.orig is None:
            exp = read_json(self.original(TABLES[0]), []) or []
            drop = read_json(self.original(TABLES[1]), []) or []
            if not isinstance(exp, list) or not isinstance(drop, list):
                raise ValueError("LvExpCorrectionConfig / DropAttenuationConfig do not look like the expected tables.")
            self.orig = {"Exp": exp, "Drop": drop}

    def _exp_rates(self, rows):
        """{Difference: percent} of the EXP rows (Difference = -_iBaseLvGap)."""
        return {-int(_num(r.get("_iBaseLvGap"))): _raw_to_pct(_num(r.get("_iLvDifferenceCorrection"), 10000)) for r in rows}

    def _drop_plans(self, rows):
        """{plan id: [(gap_low, gap_high, ratio per 10000), ...]} sorted by gap."""
        plans = {}
        for r in rows:
            a, b = int(_num(_first(r.get("_kLvDiffLowerlimit")))), int(_num(_first(r.get("_kLvDiffUpLimit"))))
            plans.setdefault(int(_num(r.get("_iPlanID"))), []).append((min(a, b), max(a, b), int(_num(r.get("_iAttenuationRatio")))))
        return {p: sorted(v) for p, v in plans.items()}

    def _plan_rates(self, ranges):
        """{Difference: percent} for -999..999 from a plan's gap ranges (a gap no range covers is 100%)."""
        rates = {}
        for d in range(DROP_RANGE[0], DROP_RANGE[1] + 1):
            rates[d] = 100.0
        for lo, hi, ratio in ranges:
            for gap in range(max(lo, DROP_RANGE[0]), min(hi, DROP_RANGE[1]) + 1):
                rates[-gap] = _raw_to_pct(ratio)
        return rates

    def _plan_usage(self):
        """{plan id: [DropGroupConfig row ids that name it]}, read from the live DropGroupConfig (the Mobs page edits it)."""
        p = self.live("DropGroupConfig")
        try:
            data = read_json(p, {}) or {}
        except (OSError, ValueError):
            return {}
        rows = [r for v in data.values() for r in (v if isinstance(v, list) else [v])] if isinstance(data, dict) else data
        use = {}
        for r in rows:
            if isinstance(r, dict):
                plan = int(_num(r.get("_iAttenuationPlanID")))
                if plan > 0:
                    use.setdefault(plan, []).append(int(_num(r.get("_iID"))))
        return use

    def preview(self, kind, plan, steps):
        """The steps as ranges, highest Difference first: [{"from": low, "to": high, "rate": percent}], plus the span."""
        with LOCK:
            lo, hi = EXP_RANGE if kind == "exp" else DROP_RANGE
            st = clean_steps(steps, lo, hi)
            runs = []
            for d in range(hi, lo - 1, -1):
                r = rate_at(st, d)
                if runs and runs[-1]["rate"] == r:
                    runs[-1]["from"] = d
                else:
                    runs.append({"from": d, "to": d, "rate": r})
            return {"runs": runs, "low": lo, "high": hi}

    # ------------------------------------------------------------ reading for the page
    def get(self):
        with LOCK:
            self._load()
            ov = self.load_overrides()
            exp_orig = derive_steps(self._exp_rates(self.orig["Exp"]))
            plans = self._drop_plans(self.orig["Drop"])
            use = self._plan_usage()
            drop = {}
            for p in sorted(plans):
                o = derive_steps(self._plan_rates(plans[p]))
                cur = ov["Drop"].get(str(p))
                drop[str(p)] = {"original": _steps_json(o), "steps": _steps_json(cur if cur is not None else o), "edited": cur is not None,
                                "usedBy": use.get(p, [])}
            return {"exp": {"original": _steps_json(exp_orig),
                            "steps": _steps_json(ov["Exp"] if ov["Exp"] is not None else exp_orig),
                            "edited": ov["Exp"] is not None, "range": list(EXP_RANGE)},
                    "drop": drop, "dropRange": list(DROP_RANGE), "rateMax": RATE_MAX, "meta": self.meta()}

    def meta(self):
        with LOCK:
            ov = self.load_overrides()
            st = self.load_state()
            status = self.file_status()
            nothing = not self.count(ov) and status == "pristine"
            return {"home": str(self.home), "fileStatus": status,
                    "inSync": status != "external" and (nothing or st.get("applied_overrides") == self.ov_hash(ov)),
                    "serverRunning": server_running(), "changed": self.count(ov), "overridesFile": str(OVERRIDES),
                    "rateMax": RATE_MAX}

    def check(self, kind, plan, steps, player_level, monster_level):
        """What a player of one level gets from a monster of another, with the given (or saved) steps."""
        with LOCK:
            self._load()
            player_level, monster_level = int(player_level), int(monster_level)
            if not (1 <= player_level <= 999 and 1 <= monster_level <= 999):
                raise ValueError("Levels must be between 1 and 999.")
            d = monster_level - player_level
            if kind == "exp":
                st = clean_steps(steps, *EXP_RANGE)
                d = max(EXP_RANGE[0], min(EXP_RANGE[1], d))      # the server clamps the gap to -200..200
            else:
                st = clean_steps(steps, *DROP_RANGE)
            return {"difference": monster_level - player_level, "rate": rate_at(st, d)}

    # ------------------------------------------------------------ editing
    def set_steps(self, kind, plan, steps):
        with LOCK:
            self._load()
            ov = self.load_overrides()
            if kind == "exp":
                new = clean_steps(steps, *EXP_RANGE)
                orig = derive_steps(self._exp_rates(self.orig["Exp"]))
                ov["Exp"] = None if new == [(d, float(r)) for d, r in orig] else new
            elif kind == "drop":
                plans = self._drop_plans(self.orig["Drop"])
                try:
                    pid = int(plan)
                except (TypeError, ValueError):
                    raise ValueError("Pick a plan.")
                if pid not in plans:
                    raise ValueError(f"Plan {pid} does not exist. Only the existing plans can be edited.")
                new = clean_steps(steps, *DROP_RANGE)
                orig = derive_steps(self._plan_rates(plans[pid]))
                if new == [(d, float(r)) for d, r in orig]:
                    ov["Drop"].pop(str(pid), None)
                else:
                    ov["Drop"][str(pid)] = new
            else:
                raise ValueError("Unknown table.")
            self.save_overrides(ov)
            return self.get()

    def reset(self, kind, plan=None):
        with LOCK:
            self._load()
            ov = self.load_overrides()
            if kind == "exp":
                ov["Exp"] = None
            elif kind == "drop" and plan is not None:
                ov["Drop"].pop(str(int(plan)), None)
            else:
                raise ValueError("Unknown table.")
            self.save_overrides(ov)
            return self.get()

    def reset_all(self):
        with LOCK:
            self._load()
            self.save_overrides({"Exp": None, "Drop": {}})
            return self.get()

    # ------------------------------------------------------------ building the tables
    def build_exp(self, steps):
        rows = []
        for r in self.orig["Exp"]:
            n = dict(r)
            d = -int(_num(r.get("_iBaseLvGap")))
            n["_iLvDifferenceCorrection"] = _pct_to_raw(rate_at(steps, d))
            rows.append(n)
        return rows

    def build_drop(self, edits):
        """Original rows of untouched plans stay as they are. An edited plan is rebuilt as one row per run of equal rate,
        in gap order, reusing the plan's old row ids first (then new ids above the table's highest)."""
        rows = [dict(r) for r in self.orig["Drop"]]
        top = max([int(_num(r.get("_iID"))) for r in rows] or [0])
        out = []
        done = set()
        for r in rows:
            pid = int(_num(r.get("_iPlanID")))
            if str(pid) not in edits:
                out.append(r)
                continue
            if pid in done:
                continue
            done.add(pid)
            ids = [int(_num(x.get("_iID"))) for x in rows if int(_num(x.get("_iPlanID"))) == pid]
            runs = []
            for gap in range(DROP_RANGE[0], DROP_RANGE[1] + 1):       # gap order, low to high
                pct = rate_at(edits[str(pid)], -gap)
                raw = _pct_to_raw(pct)
                if runs and runs[-1][2] == raw:
                    runs[-1][1] = gap
                else:
                    runs.append([gap, gap, raw])
            for lo, hi, raw in runs:
                if ids:
                    rid = ids.pop(0)
                else:
                    top += 1
                    rid = top
                # the original files name the two limits the other way round: Lowerlimit is the higher gap
                out.append({"_iAttenuationRatio": raw, "_iID": rid, "_iPlanID": pid,
                            "_kLvDiffLowerlimit": [hi], "_kLvDiffUpLimit": [lo]})
            # ids left over (the plan got fewer rows) are simply not used any more
        return out

    # ------------------------------------------------------------ apply
    def _backup(self, table, stamp):
        self.backups.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.live(table), self.backups / f"{table}-{stamp}.json")
        for old in sorted(self.backups.glob(f"{table}-*.json"))[:-MAX_BACKUPS]:
            old.unlink(missing_ok=True)

    def apply(self):
        with LOCK:
            self._load()
            if self.file_status() == "external":
                raise RuntimeError("A level penalty table was changed outside this tool. Use 'Use current files as new original' first.")
            ov = self.load_overrides()
            want = {}
            if ov["Exp"] is not None:
                want[TABLES[0]] = _dump(self.build_exp(ov["Exp"]))
            if ov["Drop"]:
                want[TABLES[1]] = _dump(self.build_drop(ov["Drop"]))
            st = self.ensure_original_all()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for t in TABLES:
                s = st["tables"][t]
                if t in want:
                    self._backup(t, stamp)
                    atomic_write(self.live(t), want[t])
                    s["applied_sha"] = sha256(self.live(t))
                elif s.get("applied_sha"):            # no edits left on this table: put the original back
                    self._backup(t, stamp)
                    shutil.copy2(self.original(t), self.live(t))
                    s["applied_sha"] = None
            st["applied_overrides"] = self.ov_hash(ov)
            self.save_state(st)
            return {"tables": len(want), "backup": stamp, "meta": self.meta()}

    def ensure_original_all(self):
        st = self.load_state()
        for t in TABLES:
            if not st["tables"].get(t) or not self.original(t).is_file():
                st = self.ensure_original(t)
        return st

    def restore_original(self):
        with LOCK:
            self._load()
            if self.file_status() == "external":
                raise RuntimeError("A level penalty table was changed outside this tool. Use 'Use current files as new original' first.")
            st = self.load_state()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            for t, s in st["tables"].items():
                if s.get("applied_sha") and self.original(t).is_file():
                    self._backup(t, stamp)
                    shutil.copy2(self.original(t), self.live(t))
                    s["applied_sha"] = None
            st["applied_overrides"] = None
            self.save_state(st)
            return self.meta()
