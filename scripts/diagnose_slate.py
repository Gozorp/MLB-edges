"""
Diagnose why games get filtered out of the slate. Prints every game with
model_prob, fair_prob, edge, tier, and the gate that rejected it (if any).
"""
from __future__ import annotations

import argparse
from datetime import datetime

import numpy as np
import pandas as pd

from mlb_edge import model as md
from mlb_edge import build_pipeline as bp
from mlb_edge import data_ingestion as di
from mlb_edge.edge_calculator import score_conviction, shin
from mlb_edge.stadiums import normalize_team
from mlb_edge.config import (
    MIN_EDGE_PCT, MAX_EDGE_PCT, MIN_FAIR_PROB, MIN_MODEL_PROB, MAX_MODEL_PROB,
    TIER_SIZES,
)


def diagnose(date_str: str, model_path: str) -> None:
    day = datetime.strptime(date_str, "%Y-%m-%d").date()
    stage1, stage2 = md.load(model_path)

    games = bp.build_slate_frame(day)
    games = md.predict(stage1, stage2, games)

    client = di.OddsClient()
    odds = client.current_lines()
    odds["outcome"] = odds["outcome"].apply(normalize_team)

    h2h = odds[odds["market"] == "h2h"].copy()
    p = h2h["price"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        dec = np.where(p > 0, 1.0 + p / 100.0, 1.0 + 100.0 / np.abs(p))
    dec[~np.isfinite(dec)] = np.nan
    h2h["decimal"] = dec
    h2h["home_team_abbr"] = h2h["home_team"].apply(normalize_team)
    h2h["away_team_abbr"] = h2h["away_team"].apply(normalize_team)
    h2h["commence_date"] = (pd.to_datetime(h2h["commence_time"], utc=True)
                            .dt.tz_convert("America/New_York").dt.date)
    keys = ["home_team_abbr", "away_team_abbr", "commence_date"]
    pivot = (h2h.pivot_table(index=keys, columns="outcome",
                             values="decimal", aggfunc="median")
             .reset_index())

    g = games.copy()
    g["home_team_abbr"] = g["home_team"].apply(normalize_team)
    g["away_team_abbr"] = g["away_team"].apply(normalize_team)
    g["game_date_only"] = pd.to_datetime(g["game_date"]).dt.date
    merged = g.merge(pivot,
                     left_on=["home_team_abbr", "away_team_abbr", "game_date_only"],
                     right_on=keys, how="left", suffixes=("", "_odds"))

    rows = []
    for _, r in merged.sort_values("model_prob", ascending=False).iterrows():
        home_dec = r.get(normalize_team(r["home_team"]))
        away_dec = r.get(normalize_team(r["away_team"]))
        model_p = r["model_prob"]

        reason = ""
        side = tier = signals_str = ""
        fair = edge = np.nan

        if pd.isna(home_dec) or pd.isna(away_dec):
            reason = "no_odds"
        else:
            p_home_raw = 1.0 / home_dec
            p_away_raw = 1.0 / away_dec
            p_home_fair, p_away_fair = shin(p_home_raw, p_away_raw)

            if model_p >= 0.5:
                side, dec_, fair, p_model = "home", home_dec, p_home_fair, model_p
            else:
                side, dec_, fair, p_model = "away", away_dec, p_away_fair, 1 - model_p

            edge = p_model - fair if pd.notna(fair) else np.nan

            if not (MIN_MODEL_PROB <= p_model <= MAX_MODEL_PROB):
                reason = f"model_prob_out_of_band({p_model:.3f})"
            elif pd.isna(fair) or fair < MIN_FAIR_PROB:
                reason = f"fair_too_low({fair:.3f})"
            elif pd.isna(edge) or edge < MIN_EDGE_PCT:
                reason = f"edge_too_small({edge*100:.2f}pp)"
            elif edge > MAX_EDGE_PCT:
                reason = f"edge_too_big({edge*100:.2f}pp)"
            else:
                persp = r.copy()
                if side == "away":
                    for col in ["sp_xera_gap", "team_woba_gap",
                                "sp_k_bb_pct_gap", "sp_siera_gap", "sp_fip_gap"]:
                        if col in persp:
                            persp[col] = -persp[col]
                    persp["home_sp_luck"], persp["away_sp_luck"] = (
                        persp.get("away_sp_luck"), persp.get("home_sp_luck"),
                    )
                conviction = score_conviction(persp)
                tier = conviction.tier
                signals_str = ", ".join(conviction.signals_fired)
                if TIER_SIZES[tier] == 0:
                    reason = f"tier_SKIP_or_GOLD({tier})"
                else:
                    reason = "BET"

        rows.append({
            "matchup": f"{r['away_team']}@{r['home_team']}",
            "side": side,
            "model_p": round(model_p, 4),
            "fair": round(fair, 4) if pd.notna(fair) else np.nan,
            "edge_pp": round(edge * 100, 2) if pd.notna(edge) else np.nan,
            "tier": tier,
            "signals": signals_str[:50],
            "result": reason,
        })

    df = pd.DataFrame(rows)
    with pd.option_context("display.width", 200, "display.max_colwidth", 50,
                           "display.max_rows", 50):
        print(df.to_string(index=False))

    print("\n--- GATE ATTRITION ---")
    counts = df["result"].str.split("(").str[0].value_counts()
    for k, v in counts.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--model_path", required=True)
    args = ap.parse_args()
    diagnose(args.date, args.model_path)
