"""
backtest_f4.py
--------------
Calibration backtest for the F4 conviction signal (SP luck regression).

F4 fires when:
  * our SP's ERA - xERA >= 1.0 (we expect him to pitch better), OR
  * opp SP's ERA - xERA <= -1.0 (we expect opp to regress worse)

with both starters having >= 800 pitches of sample.

This script measures: across all historical games where F4 fires for ONE
side cleanly (not both), how often does that side actually win?

Decision rule (auto-applied):
  - hit rate >= 0.55  -> signal is sharp, leave alone
  - 0.52 <= hit rate < 0.55 -> signal calibrated, leave alone
  - 0.50 <= hit rate < 0.52 -> marginal, raise threshold from 1.0 -> 1.5
  - hit rate < 0.50 -> noise, raise threshold to 2.0 (effectively kill it)

Tightening the threshold is automatic when warranted; the script prints
exactly what it changed and why.

Usage:
    python scripts/backtest_f4.py
    python scripts/backtest_f4.py --no-apply   # diagnostic only, no edit
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)


def load_features() -> pd.DataFrame:
    """Combine all available historical feature caches. Prefer v11 where
    available, fall back to v10/v9 (the SP luck features are unchanged
    across these versions, so the F4 backtest is valid)."""
    cache_dir = ROOT / "data" / "feature_cache"
    frames = []
    # Per season pick the newest version available
    for season in (2023, 2024, 2025):
        for ver in ("v11", "v10", "v9"):
            for pattern in (f"features_{season}_full_1_{ver}.parquet",
                            f"features_{season}_*_1_{ver}.parquet"):
                candidates = sorted(cache_dir.glob(pattern))
                if candidates:
                    df = pd.read_parquet(candidates[-1])
                    df["__season"] = season
                    df["__version"] = ver
                    frames.append(df)
                    print(f"  loaded {candidates[-1].name}  ({len(df)} games)")
                    break
            else:
                continue
            break
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def f4_backtest(games: pd.DataFrame,
                luck_threshold: float = 1.0,
                n_pitches_min: int = 800) -> dict:
    """For each game, determine if F4 fires for home, away, both, or neither.
    Then measure win rate when F4 picks one side cleanly."""
    needed = ["home_sp_luck", "away_sp_luck", "home_sp_n_pitches",
              "away_sp_n_pitches", "home_win"]
    games = games.dropna(subset=needed).copy()

    # Logic mirrors edge_calculator.score_conviction's F4 block:
    # for "home" perspective, fires when home_luck >= +T (home unlucky, due
    # to improve) OR away_luck <= -T (away lucky, due to regress worse) —
    # with the relevant SP having credible sample.
    home_unlucky = (games["home_sp_luck"] >= luck_threshold) & (games["home_sp_n_pitches"] >= n_pitches_min)
    away_lucky   = (games["away_sp_luck"] <= -luck_threshold) & (games["away_sp_n_pitches"] >= n_pitches_min)
    home_f4      = home_unlucky | away_lucky

    away_unlucky = (games["away_sp_luck"] >= luck_threshold) & (games["away_sp_n_pitches"] >= n_pitches_min)
    home_lucky   = (games["home_sp_luck"] <= -luck_threshold) & (games["home_sp_n_pitches"] >= n_pitches_min)
    away_f4      = away_unlucky | home_lucky

    # Clean-pick cases (only one side fires)
    f4_picks_home = home_f4 & ~away_f4
    f4_picks_away = away_f4 & ~home_f4
    f4_both       = home_f4 & away_f4
    f4_neither    = ~home_f4 & ~away_f4

    # Outcomes
    home_picks = games[f4_picks_home]
    away_picks = games[f4_picks_away]

    home_wins = int(home_picks["home_win"].sum())
    home_n    = len(home_picks)
    away_wins = int((1 - away_picks["home_win"]).sum())
    away_n    = len(away_picks)

    total_picks = home_n + away_n
    total_wins  = home_wins + away_wins
    hit_rate    = total_wins / max(total_picks, 1)

    # Baseline: home win rate in cases where F4 didn't fire / both fired
    baseline_n = len(games[~(f4_picks_home | f4_picks_away)])
    baseline_home_wins = int(games[~(f4_picks_home | f4_picks_away)]["home_win"].sum())
    baseline_home_rate = baseline_home_wins / max(baseline_n, 1)

    return {
        "luck_threshold": luck_threshold,
        "n_pitches_min": n_pitches_min,
        "total_games": int(len(games)),
        "f4_picks_home": int(home_n),
        "f4_picks_home_wins": home_wins,
        "f4_picks_away": int(away_n),
        "f4_picks_away_wins": away_wins,
        "f4_both_fired": int(f4_both.sum()),
        "f4_neither":   int(f4_neither.sum()),
        "total_f4_picks": int(total_picks),
        "total_f4_wins":  int(total_wins),
        "hit_rate": round(hit_rate, 4),
        "baseline_home_rate_when_no_f4": round(baseline_home_rate, 4),
    }


def decide_action(hit_rate: float) -> tuple[str, float | None]:
    """Return (verdict, new_threshold_or_None)."""
    if hit_rate >= 0.55:
        return "SHARP — F4 working well, no change", None
    if hit_rate >= 0.52:
        return "CALIBRATED — slight edge, no change", None
    if hit_rate >= 0.50:
        return "MARGINAL — tightening threshold to 1.5", 1.5
    return "NOISE — tightening threshold to 2.0 (effectively kills F4)", 2.0


def apply_threshold_change(new_threshold: float) -> bool:
    """Edit config.py to change CONVICTION.pitcher_luck_max.

    NOTE: pitcher_luck_max is stored as a NEGATIVE number (-1.0 by default)
    because the conviction filter compares `our_luck >= -CONVICTION.pitcher_luck_max`
    and `opp_luck <= CONVICTION.pitcher_luck_max`. So tightening from 1.0 to
    1.5 means setting pitcher_luck_max = -1.5.
    """
    cfg = ROOT / "mlb_edge" / "config.py"
    txt = cfg.read_text(encoding="utf-8")
    pattern = r"pitcher_luck_max:\s*float\s*=\s*-?\d+\.?\d*"
    new_value = -new_threshold
    if not re.search(pattern, txt):
        print(f"  ❌ couldn't find pitcher_luck_max line in config.py")
        return False
    new_txt = re.sub(pattern, f"pitcher_luck_max: float = {new_value}", txt)
    cfg.write_text(new_txt, encoding="utf-8")
    print(f"  ✅ config.py updated: pitcher_luck_max = {new_value} "
          f"(F4 now requires |luck| >= {new_threshold})")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-apply", action="store_true",
                    help="Diagnostic only — don't edit config.py")
    args = ap.parse_args()

    print("━" * 70)
    print(f"  F4 (SP luck regression) calibration backtest")
    print(f"  Run: {datetime.now().isoformat(timespec='seconds')}")
    print("━" * 70)
    print()
    print("Loading historical feature caches:")
    games = load_features()
    if games.empty:
        print("ERROR: no historical feature caches found")
        return 1

    print()
    print(f"Total games loaded: {len(games)}")
    print()

    # Run at the current threshold
    res = f4_backtest(games, luck_threshold=1.0, n_pitches_min=800)

    print("━" * 70)
    print(f"  RESULTS at current thresholds (|luck| >= 1.0, n_pitches >= 800)")
    print("━" * 70)
    print(f"  Total games considered:           {res['total_games']:>5}")
    print(f"  F4 picked HOME side:              {res['f4_picks_home']:>5}  "
          f"(home wins: {res['f4_picks_home_wins']})")
    print(f"  F4 picked AWAY side:              {res['f4_picks_away']:>5}  "
          f"(away wins: {res['f4_picks_away_wins']})")
    print(f"  F4 fired both sides (ambiguous):  {res['f4_both_fired']:>5}")
    print(f"  F4 didn't fire:                   {res['f4_neither']:>5}")
    print()
    print(f"  Total F4 picks:                   {res['total_f4_picks']:>5}")
    print(f"  Total F4 wins:                    {res['total_f4_wins']:>5}")
    print(f"  ▶▶ F4 hit rate:                   {res['hit_rate']:.1%}")
    print(f"  Baseline (no-F4 home rate):       {res['baseline_home_rate_when_no_f4']:.1%}")
    print()

    verdict, new_thresh = decide_action(res["hit_rate"])
    print("━" * 70)
    print(f"  VERDICT: {verdict}")
    print("━" * 70)

    if new_thresh is not None and not args.no_apply:
        print()
        print(f"Applying threshold change to config.py:")
        applied = apply_threshold_change(new_thresh)
        if applied:
            # Re-run backtest at the new threshold to project effect
            print()
            new_res = f4_backtest(games, luck_threshold=new_thresh,
                                  n_pitches_min=800)
            print(f"  At new threshold |luck| >= {new_thresh}:")
            print(f"    F4 picks: {new_res['total_f4_picks']:>5} "
                  f"(was {res['total_f4_picks']})")
            print(f"    Hit rate: {new_res['hit_rate']:.1%} "
                  f"(was {res['hit_rate']:.1%})")
    elif new_thresh is not None:
        print(f"\n  (--no-apply: skipping config.py edit. Threshold "
              f"{new_thresh} would have been applied.)")

    # Persist results to metrics dir for tracking
    metrics_dir = ROOT / "metrics"
    metrics_dir.mkdir(exist_ok=True)
    log_path = metrics_dir / "f4_calibration.jsonl"
    entry = {"ts": datetime.now().isoformat(timespec="seconds"),
             "hit_rate": res["hit_rate"], "n": res["total_f4_picks"],
             "verdict": verdict,
             "new_threshold_applied": new_thresh if not args.no_apply else None}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"\n📊 Logged to metrics/f4_calibration.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
