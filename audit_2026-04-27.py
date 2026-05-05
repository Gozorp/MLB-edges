"""Print full audit of 2026-04-25 slate using v10 features (and current model)."""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import joblib

sys.path.insert(0, str(Path(__file__).parent))

from datetime import date
from mlb_edge.build_pipeline import build_slate_frame
from mlb_edge.edge_calculator import score_conviction
from mlb_edge.market_analysis import shin

day = date(2026, 4, 27)

# Load model + features
print("Building v10 slate...")
games = build_slate_frame(day, include_weather=True)
print(f"Built {len(games)} games")

models = joblib.load("models/latest.pkl")
from mlb_edge.model import predict as mlb_predict
games = mlb_predict(models["stage1"], models["stage2"], games)

# Get odds for the day - read from cache. Glob pattern is built FROM the
# `day` variable above so it tracks any date change, not a hardcoded literal.
# (Bug fix: previous hardcoded "2026-04-25" pulled stale odds when the
# nightly_backstop cloned this script with a date-replaced `day`.)
import glob
odds_files = sorted(glob.glob(f"data/odds_cache/odds_{day:%Y-%m-%d}*.parquet"))
odds = pd.read_parquet(odds_files[-1]) if odds_files else None
if odds is None:
    print("No odds cache — using model_prob only for audit")
    odds_h2h = pd.DataFrame()
else:
    odds_h2h = odds[odds["market"] == "h2h"].copy()

# Build audit
rows = []
for _, g in games.iterrows():
    # Determine pick
    p_home = g["model_prob"]
    pick = g["home_team"] if p_home >= 0.5 else g["away_team"]
    pick_prob = p_home if p_home >= 0.5 else 1 - p_home

    # Devig home implied
    home_implied = np.nan
    home_edge_pp = np.nan
    if not odds_h2h.empty:
        from mlb_edge.stadiums import normalize_team
        home_n = normalize_team(g["home_team"])
        away_n = normalize_team(g["away_team"])
        match = odds_h2h[
            (odds_h2h["home_team"].apply(normalize_team) == home_n) &
            (odds_h2h["away_team"].apply(normalize_team) == away_n)
        ]
        if not match.empty:
            home_dec = 1 + match[match["outcome"].apply(normalize_team) == home_n]["price"].iloc[0] / 100 \
                if (match[match["outcome"].apply(normalize_team) == home_n]["price"].iloc[0] > 0) \
                else 1 + 100 / abs(match[match["outcome"].apply(normalize_team) == home_n]["price"].iloc[0])
            away_dec = 1 + match[match["outcome"].apply(normalize_team) == away_n]["price"].iloc[0] / 100 \
                if (match[match["outcome"].apply(normalize_team) == away_n]["price"].iloc[0] > 0) \
                else 1 + 100 / abs(match[match["outcome"].apply(normalize_team) == away_n]["price"].iloc[0])
            p_home_raw = 1.0 / home_dec
            p_away_raw = 1.0 / away_dec
            ph_fair, _ = shin(p_home_raw, p_away_raw)
            home_implied = ph_fair * 100
            home_edge_pp = (p_home - ph_fair) * 100

    # Conviction signals (home perspective by default)
    perspective = g.copy()
    if p_home < 0.5:
        for col in ["sp_xera_gap", "team_woba_gap", "sp_k_bb_pct_gap",
                    "sp_siera_gap", "sp_fip_gap",
                    # v11: bullpen gaps must flip for away picks too —
                    # otherwise audit reports false F5 vetoes (e.g. CLE
                    # PLATINUM was misreported as SKIP on 2026-04-26).
                    "bullpen_siera_gap", "bullpen_xwoba_gap",
                    "bullpen_k_pct_gap", "bullpen_bb_pct_gap",
                    "bullpen_hardhit_gap", "bullpen_fatigue_gap"]:
            if col in perspective:
                perspective[col] = -perspective[col]
        perspective["home_sp_luck"], perspective["away_sp_luck"] = (
            perspective.get("away_sp_luck"), perspective.get("home_sp_luck"))
        # F1 and F4 conviction gates read home_sp_n_pitches/away_sp_n_pitches
        # directly. Must swap (not negate) for away picks.
        perspective["home_sp_n_pitches"], perspective["away_sp_n_pitches"] = (
            perspective.get("away_sp_n_pitches"),
            perspective.get("home_sp_n_pitches"))
        perspective["home_bullpen_n_pitches"], perspective["away_bullpen_n_pitches"] = (
            perspective.get("away_bullpen_n_pitches"),
            perspective.get("home_bullpen_n_pitches"))
    conv = score_conviction(perspective)

    rows.append({
        "away": g["away_team"],
        "home": g["home_team"],
        "pick": pick,
        "pick_prob": round(pick_prob * 100, 1),
        "home_implied": round(home_implied, 1) if pd.notna(home_implied) else "—",
        "home_edge_pp": round(home_edge_pp, 2) if pd.notna(home_edge_pp) else "—",
        "tier": conv.tier,
        "signals": ", ".join(conv.signals_fired),
        "notes": " | ".join(conv.notes),
    })

audit = pd.DataFrame(rows).sort_values(
    by="home_edge_pp",
    key=lambda x: pd.to_numeric(x, errors="coerce").fillna(-999),
    ascending=False,
)
print("\n=== v10 AUDIT 2026-04-25 ===")
print(audit.to_string(index=False))
audit.to_csv("audit_2026-04-27.csv", index=False)
print("\nSaved to audit_2026-04-26.csv")
