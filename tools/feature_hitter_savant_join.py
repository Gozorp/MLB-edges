#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
feature_hitter_savant_join.py   (Phase 2 of the hitter-status feature)
----------------------------------------------------------------------
Joins the harvested per-hitter Statcast metrics into the batter payload, plus
adds the three non-Savant columns (Pos, K%, BB%) from data statsapi already
returns -- so each top-5 hitter in *_top_5_batters_json gains:
    pos, k_pct, bb_pct, ev, la, hard_hit_pct, bbe, xwoba, xba, xslg, sprint

Fail-safe everywhere: a missing savant CSV or a player not on the leaderboard
just leaves those fields null (frontend renders "-"); never raises in the bake.

6 idempotent edits to mlb_edge/platoon_splits.py. Run from repo root.
"""
import sys

F = "mlb_edge/platoon_splits.py"
EDITS = []  # (old, new, sentinel)

# --- Edit 1: module-level Savant loader (insert before get_career_splits) ----
LOADER = '''# ---------------------------------------------------------------------------
# Savant per-hitter Statcast leaderboard (harvested by the savant-hitters cron
# into data/savant_hitters_<year>.csv). Read once, fail-safe: a missing file
# just means hitters carry no Savant fields.
# ---------------------------------------------------------------------------
_SAVANT_HITTERS = None


def _load_savant_hitters() -> dict:
    global _SAVANT_HITTERS
    if _SAVANT_HITTERS is not None:
        return _SAVANT_HITTERS
    import csv as _csv
    import glob as _glob
    from datetime import datetime as _dt, timezone as _tz
    _SAVANT_HITTERS = {}
    yr = _dt.now(_tz.utc).year
    candidates = [Path(f"data/savant_hitters_{yr}.csv")]
    candidates += [Path(p) for p in sorted(_glob.glob("data/savant_hitters_*.csv"),
                                           reverse=True)]
    for p in candidates:
        if not p.exists():
            continue
        try:
            with open(p, newline="", encoding="utf-8") as fh:
                for row in _csv.DictReader(fh):
                    try:
                        pid = int(float(row["player_id"]))
                    except (TypeError, ValueError, KeyError):
                        continue
                    rec = {}
                    for k in ("ev", "la", "hard_hit_pct", "bbe",
                              "xwoba", "xba", "xslg", "sprint"):
                        v = (row.get(k) or "").strip()
                        if v in ("", "NA", "nan"):
                            continue
                        try:
                            rec[k] = round(float(v), 3)
                        except ValueError:
                            rec[k] = v
                    _SAVANT_HITTERS[pid] = rec
            break
        except Exception as e:  # noqa: BLE001
            log.debug("[platoon_splits] savant hitters load failed (%s): %s", p, e)
    return _SAVANT_HITTERS


def get_career_splits(player_id: int) -> dict:'''
EDITS.append(("def get_career_splits(player_id: int) -> dict:", LOADER,
              "_load_savant_hitters"))

# --- Edit 2: cache version gate (refresh entries lacking k_pct) --------------
EDITS.append(('if cached is not None and "season_PA" in cached:',
              'if cached is not None and "k_pct" in cached:',
              '"k_pct" in cached'))

# --- Edit 3: out-init defaults for the new fields ---------------------------
EDITS.append((
    '    out = {"vs_LHP": {"OPS": 0.0, "PA": 0, "AVG": 0.0},\n'
    '           "vs_RHP": {"OPS": 0.0, "PA": 0, "AVG": 0.0},\n'
    '           "bat_side": None}',
    '    out = {"vs_LHP": {"OPS": 0.0, "PA": 0, "AVG": 0.0},\n'
    '           "vs_RHP": {"OPS": 0.0, "PA": 0, "AVG": 0.0},\n'
    '           "bat_side": None, "pos": None, "k_pct": None, "bb_pct": None}',
    '"bat_side": None, "pos": None'))

# --- Edit 4: capture primary position alongside bat_side --------------------
EDITS.append((
    '            bs = (people[0].get("batSide") or {}).get("code")\n'
    '            out["bat_side"] = bs',
    '            bs = (people[0].get("batSide") or {}).get("code")\n'
    '            out["bat_side"] = bs\n'
    '            out["pos"] = (people[0].get("primaryPosition") or {}).get("abbreviation")',
    'out["pos"] = (people[0].get("primaryPosition")'))

# --- Edit 5: derive K%/BB% from the season-hitting object already fetched ----
EDITS.append((
    '                try:\n'
    '                    out["season_PA"] = int(st.get("plateAppearances", 0) or 0)\n'
    '                except (TypeError, ValueError):\n'
    '                    pass',
    '                try:\n'
    '                    out["season_PA"] = int(st.get("plateAppearances", 0) or 0)\n'
    '                except (TypeError, ValueError):\n'
    '                    pass\n'
    '                try:\n'
    '                    _pa = int(st.get("plateAppearances", 0) or 0)\n'
    '                    if _pa > 0:\n'
    '                        out["k_pct"] = round(100.0 * int(st.get("strikeOuts", 0) or 0) / _pa, 1)\n'
    '                        out["bb_pct"] = round(100.0 * int(st.get("baseOnBalls", 0) or 0) / _pa, 1)\n'
    '                except (TypeError, ValueError):\n'
    '                    pass',
    'out["k_pct"] = round(100.0'))

# --- Edit 6: join savant + pos/K%/BB% into the per-batter dict ---------------
EDITS.append((
    '        out.append({\n'
    '            "order": pos,\n'
    '            "name": name,\n',
    '        _sav = _load_savant_hitters().get(int(pid), {})\n'
    '        out.append({\n'
    '            "order": pos,\n'
    '            "name": name,\n'
    '            "pos": splits.get("pos"),\n'
    '            "k_pct": splits.get("k_pct"),\n'
    '            "bb_pct": splits.get("bb_pct"),\n'
    '            "ev": _sav.get("ev"), "la": _sav.get("la"),\n'
    '            "hard_hit_pct": _sav.get("hard_hit_pct"), "bbe": _sav.get("bbe"),\n'
    '            "xwoba": _sav.get("xwoba"), "xba": _sav.get("xba"),\n'
    '            "xslg": _sav.get("xslg"), "sprint": _sav.get("sprint"),\n',
    '_sav = _load_savant_hitters().get(int(pid), {})'))


def main():
    with open(F, "r", encoding="utf-8", newline="") as fh:
        raw = fh.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    applied = skipped = 0
    for old, new, sentinel in EDITS:
        if sentinel in work:
            print(f"  skip (already applied): {sentinel[:42]}")
            skipped += 1
            continue
        n = work.count(old)
        if n != 1:
            print(f"  ERROR anchor count={n} (need 1): {sentinel[:42]}")
            sys.exit(1)
        work = work.replace(old, new, 1)
        applied += 1
        print(f"  applied: {sentinel[:42]}")
    if applied:
        with open(F, "w", encoding="utf-8", newline="") as fh:
            fh.write(work.replace("\n", nl))
    print(f"DONE applied={applied} skipped={skipped}")
    if applied == 0 and skipped == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
