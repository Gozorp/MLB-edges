"""
bullpen_fatigue_blocker.py — 72h high-leverage bullpen workload ceiling (v5.1).

Sums pitches thrown by each team's top-3 high-leverage relievers over a
rolling 72-hour window. If the total exceeds the workload limit, the team's
maximum conviction tier is capped (so fatigued bullpens cannot anchor a
PLATINUM/DIAMOND bet).
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

LEVERAGE_THRESHOLD = 1.5
WORKLOAD_PITCH_LIMIT = 40
LOOKBACK_HOURS = 72

TIER_RANK = {"DIAMOND": 4, "PLATINUM": 3, "GOLD": 2, "SKIP": 1}


def compute_bullpen_workload(
    pitch_log_df: pd.DataFrame,
    slate_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    pitch_log_df columns: game_date, team, pitcher_id, is_starter, pitches,
    leverage_index. Returns one row per team with top-3 HL pen pitches and
    a ceiling tier.
    """
    cutoff = slate_date - timedelta(hours=LOOKBACK_HOURS)
    df = pitch_log_df.copy()
    df["game_date"] = pd.to_datetime(df["game_date"])
    df = df[
        (df["game_date"] >= cutoff)
        & (df["game_date"] < slate_date)
        & (~df["is_starter"])
        & (df["leverage_index"] >= LEVERAGE_THRESHOLD)
    ]

    grp = (
        df.groupby(["team", "pitcher_id"])["pitches"]
        .sum()
        .reset_index()
        .sort_values(["team", "pitches"], ascending=[True, False])
    )
    top3 = (
        grp.groupby("team")
        .head(3)
        .groupby("team")["pitches"]
        .sum()
        .reset_index()
        .rename(columns={"pitches": "top3_pitch_total_72h"})
    )

    def ceiling(p: int) -> str:
        if p > WORKLOAD_PITCH_LIMIT * 1.5:
            return "SKIP"
        if p > WORKLOAD_PITCH_LIMIT:
            return "GOLD"
        return "DIAMOND"

    top3["ceiling_tier"] = top3["top3_pitch_total_72h"].apply(ceiling)
    return top3


def apply_bullpen_ceiling(
    picks_df: pd.DataFrame,
    workload_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    picks_df needs: home_team, away_team, pick_winner, conv_tier.
    Demotes conv_tier to ceiling_tier when picked team is over the limit.
    """
    out = picks_df.copy()
    workload_map = dict(zip(workload_df["team"], workload_df["ceiling_tier"]))
    out["bullpen_ceiling"] = out["pick_winner"].map(workload_map).fillna("DIAMOND")

    def cap(row: pd.Series) -> str:
        cur, ceil = row["conv_tier"], row["bullpen_ceiling"]
        if TIER_RANK.get(cur, 1) > TIER_RANK.get(ceil, 4):
            return ceil
        return cur

    out["conv_tier_v51"] = out.apply(cap, axis=1)
    out["bullpen_demote_reason"] = np.where(
        out["conv_tier_v51"] != out["conv_tier"],
        f"bullpen workload >{WORKLOAD_PITCH_LIMIT} pitches in {LOOKBACK_HOURS}h "
        "on top-3 HL relievers",
        "",
    )
    return out
