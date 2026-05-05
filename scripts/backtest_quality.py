"""
backtest_quality.py
-------------------
Compare quality-vs-quantity trade-offs for the conviction filter.

For every historical game (2023+2024+2025):
  1. Score with the current v11 model -> get home_win_prob
  2. Determine the bet side (home if prob >= 0.5 else away)
  3. Run the conviction filter from that perspective -> tier
  4. Cross-reference against the actual home_win outcome

Then summarize by tier filter:
  - GOLD+   (every conviction): n picks, hit rate, projected ROI
  - PLATINUM+ (drop GOLD)
  - DIAMOND only (highest quality)
  - PLATINUM+ AND model_prob >= 0.60 (quality + confidence)

ROI projection uses a proxy decimal odds of 1.95 (typical MLB ML juice).
Real ROI requires historical odds — this is a calibration estimate, not
a guarantee. Hit rate is the honest number.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from mlb_edge.edge_calculator import score_conviction
from mlb_edge.model import predict as mlb_predict


def load_features() -> pd.DataFrame:
    cache_dir = ROOT / "data" / "feature_cache"
    frames = []
    for season in (2023, 2024, 2025):
        for ver in ("v11", "v10", "v9"):
            paths = sorted(cache_dir.glob(f"features_{season}_full_1_{ver}.parquet"))
            if paths:
                df = pd.read_parquet(paths[-1])
                frames.append(df)
                print(f"  loaded {paths[-1].name}  ({len(df)} games)")
                break
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def score_game_tier(row: pd.Series, p_home: float) -> tuple[str, str]:
    """Apply the production conviction filter from the predicted side's
    perspective. Returns (tier, pick_side)."""
    perspective = row.copy()
    if p_home < 0.5:
        for col in ["sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                    "sp_siera_gap", "sp_fip_gap",
                    "bullpen_siera_gap", "bullpen_xwoba_gap",
                    "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
                    "bullpen_hardhit_gap", "bullpen_fatigue_gap"]:
            if col in perspective:
                perspective[col] = -perspective[col]
        perspective["home_sp_luck"], perspective["away_sp_luck"] = (
            perspective.get("away_sp_luck"), perspective.get("home_sp_luck"))
        perspective["home_sp_n_pitches"], perspective["away_sp_n_pitches"] = (
            perspective.get("away_sp_n_pitches"), perspective.get("home_sp_n_pitches"))
        if "home_bullpen_n_pitches" in perspective:
            perspective["home_bullpen_n_pitches"], perspective["away_bullpen_n_pitches"] = (
                perspective.get("away_bullpen_n_pitches"),
                perspective.get("home_bullpen_n_pitches"))
        side = "away"
    else:
        side = "home"
    conv = score_conviction(perspective)
    return conv.tier, side


def project_roi(hit_rate: float, decimal_odds: float = 1.95) -> float:
    """ROI per $1 staked, assuming flat $1 bets at the average decimal odds."""
    return hit_rate * (decimal_odds - 1) - (1 - hit_rate)


def main() -> int:
    print("━" * 70)
    print("  QUALITY-vs-QUANTITY BACKTEST — conviction tier breakdown")
    print("━" * 70)
    print()
    print("Loading feature caches:")
    games = load_features()
    if games.empty:
        print("ERROR: no caches found")
        return 1
    print(f"\nTotal games: {len(games)}\n")

    print("Loading v11 model:")
    models = joblib.load(ROOT / "models" / "latest.pkl")
    print("  ok\n")

    print("Scoring all games with v11 model + conviction filter…")
    # Score with the production predict function (needs feature cols available)
    pred = mlb_predict(models["stage1"], models["stage2"], games.copy())
    games["model_prob"] = pred["model_prob"].values

    # Tier each game
    tiers = []
    sides = []
    for i, r in games.iterrows():
        t, s = score_game_tier(r, r["model_prob"])
        tiers.append(t)
        sides.append(s)
    games["tier"] = tiers
    games["side"] = sides
    games["pick_won"] = np.where(
        games["side"] == "home",
        games["home_win"] == 1,
        games["home_win"] == 0,
    )

    # Filter to games we'd actually bet (no SKIP)
    bettable = games[games["tier"] != "SKIP"].copy()
    n_total = len(games)

    print(f"  {n_total} total games; {len(bettable)} non-SKIP convictions\n")

    # ── Build the comparison table ─────────────────────────────────────────
    scenarios = [
        ("GOLD+ (current)",        bettable),
        ("PLATINUM+ (drop GOLD)",  bettable[bettable["tier"].isin(["PLATINUM", "DIAMOND"])]),
        ("DIAMOND only (top quality)", bettable[bettable["tier"] == "DIAMOND"]),
        ("PLATINUM+ & prob>=60%",  bettable[bettable["tier"].isin(["PLATINUM", "DIAMOND"]) &
                                            ((bettable["model_prob"] >= 0.60) |
                                             (bettable["model_prob"] <= 0.40))]),
        ("DIAMOND & prob>=60%",    bettable[(bettable["tier"] == "DIAMOND") &
                                            ((bettable["model_prob"] >= 0.60) |
                                             (bettable["model_prob"] <= 0.40))]),
    ]

    print("━" * 70)
    print("  RESULTS — hit rate and projected ROI (assumes 1.95 decimal odds)")
    print("━" * 70)
    print(f"  {'Filter':<32} {'Picks':>7} {'Wins':>6} {'Rate':>7} {'Per yr':>7} {'ROI':>7}")
    print(f"  {'':<32} {'':>7} {'':>6} {'':>7} {'(est)':>7} {'(est)':>7}")
    print("  " + "─" * 68)

    seasons = 3.0  # 2023+2024+2025
    for name, df in scenarios:
        n = len(df)
        if n == 0:
            print(f"  {name:<32} {n:>7} {'-':>6} {'-':>7} {'-':>7} {'-':>7}")
            continue
        wins = int(df["pick_won"].sum())
        rate = wins / n
        per_year = round(n / seasons)
        roi = project_roi(rate)
        roi_str = f"{roi*100:+.1f}%"
        rate_str = f"{rate*100:.1f}%"
        bar = " ←" if name == "PLATINUM+ (drop GOLD)" or name == "DIAMOND only (top quality)" else ""
        print(f"  {name:<32} {n:>7} {wins:>6} {rate_str:>7} {per_year:>7} {roi_str:>7}{bar}")

    print()
    print("─" * 70)
    print("  How to read:")
    print("  • Picks   = bets across 3 historical seasons (2023+2024+2025)")
    print("  • Per yr  = projected bets per year if you used this filter")
    print("  • ROI     = projected return per $1 staked (assumes 1.95 odds)")
    print("  • Break-even hit rate at 1.95 odds = 51.3%")
    print("─" * 70)
    print()
    print("  RECOMMENDATION:")
    print("  Pick the row with the best ROI you're comfortable with the volume of.")
    print("  More picks = more $$ in absolute terms but harder to swallow losing")
    print("  streaks. Fewer/higher-quality picks = smaller absolute bankroll growth")
    print("  but lower drawdowns and higher per-bet ROI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
