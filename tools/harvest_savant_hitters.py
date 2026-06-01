#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_savant_hitters.py
-------------------------
Isolated harvester for the per-hitter Statcast metrics behind the dashboard's
"individual hitter status" table. Pulls three Baseball Savant batter
leaderboards via pybaseball, merges them by MLBAM player_id, and writes a single
committed CSV that the daily bake reads (a fast local lookup -- NO live Savant
fetch in the daily-slate critical path, so it can never slow or break the slate).

Columns produced (keyed by player_id):
    player_id, name, bbe, la, ev, hard_hit_pct, xwoba, xba, xslg, sprint
(K%, BB% and fielding Pos are added later from statsapi in the bake join, not
here -- those leaderboards don't carry them.)

Fail-safe: each leaderboard is fetched independently; if one errors the others
still merge. Defensive column resolution tolerates pybaseball version drift.

Usage:  python tools/harvest_savant_hitters.py [--year YYYY] [--out PATH]
Runs in CI (pybaseball>=2.2.7 is in requirements.txt). Writes
data/savant_hitters_<year>.csv by default.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def _col(df, *cands):
    """Return the first present column name from candidates, else None.
    Case-insensitive + tolerant of the 'last_name, first_name' style."""
    lower = {c.lower().strip(): c for c in df.columns}
    for c in cands:
        if c in df.columns:
            return c
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def _name_series(df):
    nc = _col(df, "last_name, first_name", "name", "player_name")
    if nc is None:
        return None
    s = df[nc].astype(str)
    # "Ward, Taylor" -> "Taylor Ward"
    if s.str.contains(",").any():
        s = s.apply(lambda v: " ".join(reversed([p.strip() for p in v.split(",", 1)])) if "," in v else v)
    return s


def _safe_fetch(fn, label):
    try:
        df = fn()
        print(f"  [{label}] {len(df)} rows, cols: {list(df.columns)[:12]}")
        return df
    except Exception as e:  # noqa: BLE001 -- harvester must never hard-fail on one source
        print(f"  [{label}] FETCH FAILED: {e}", file=sys.stderr)
        return None


def harvest(year: int):
    import pandas as pd
    from pybaseball import (
        statcast_batter_exitvelo_barrels,
        statcast_batter_expected_stats,
        statcast_sprint_speed,
    )

    frames = {}

    ev = _safe_fetch(lambda: statcast_batter_exitvelo_barrels(year, minBBE=1), "exitvelo")
    if ev is not None:
        pid = _col(ev, "player_id", "playerid", "mlbam_id")
        cols = {
            "player_id": pid,
            "bbe": _col(ev, "attempts", "batted_balls", "bbe"),
            "la": _col(ev, "avg_hit_angle", "launch_angle_avg", "avg_la"),
            "ev": _col(ev, "avg_hit_speed", "exit_velocity_avg", "avg_ev"),
            "hard_hit_pct": _col(ev, "ev95percent", "hard_hit_percent", "hardhit_percent"),
        }
        if pid:
            sub = ev[[c for c in cols.values() if c]].copy()
            sub.columns = [k for k, v in cols.items() if v]
            sub["name"] = _name_series(ev)
            frames["ev"] = sub

    es = _safe_fetch(lambda: statcast_batter_expected_stats(year, minPA=1), "expected")
    if es is not None:
        pid = _col(es, "player_id", "playerid", "mlbam_id")
        cols = {
            "player_id": pid,
            "xwoba": _col(es, "est_woba", "xwoba", "est_woba_using_speedangle"),
            "xba": _col(es, "est_ba", "xba"),
            "xslg": _col(es, "est_slg", "xslg"),
        }
        if pid:
            sub = es[[c for c in cols.values() if c]].copy()
            sub.columns = [k for k, v in cols.items() if v]
            if "name" not in (frames.get("ev").columns if "ev" in frames else []):
                sub["name"] = _name_series(es)
            frames["es"] = sub

    sp = _safe_fetch(lambda: statcast_sprint_speed(year, min_opp=1), "sprint")
    if sp is not None:
        pid = _col(sp, "player_id", "playerid", "mlbam_id", "runner_id")
        spc = _col(sp, "sprint_speed", "sprint", "feet_per_sec")
        if pid and spc:
            sub = sp[[pid, spc]].copy()
            sub.columns = ["player_id", "sprint"]
            frames["sp"] = sub

    if not frames:
        raise RuntimeError("all three leaderboards failed to fetch")

    out = None
    for f in frames.values():
        f["player_id"] = f["player_id"].astype("Int64")
        out = f if out is None else out.merge(f, on="player_id", how="outer", suffixes=("", "_dup"))
    # collapse any duplicate name columns
    if "name_dup" in out.columns:
        out["name"] = out["name"].fillna(out["name_dup"])
        out = out.drop(columns=[c for c in out.columns if c.endswith("_dup")])
    order = ["player_id", "name", "bbe", "la", "ev", "hard_hit_pct", "xwoba", "xba", "xslg", "sprint"]
    for c in order:
        if c not in out.columns:
            out[c] = pd.NA
    return out[order].dropna(subset=["player_id"]).sort_values("player_id").reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=datetime.now(timezone.utc).year)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else Path(f"data/savant_hitters_{args.year}.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[harvest_savant_hitters] year={args.year} -> {out_path}")
    df = harvest(args.year)
    df.to_csv(out_path, index=False)
    print(f"[harvest_savant_hitters] wrote {len(df)} hitters with "
          f"{df['ev'].notna().sum()} EV / {df['xwoba'].notna().sum()} xwOBA / "
          f"{df['sprint'].notna().sum()} sprint values")


if __name__ == "__main__":
    main()
