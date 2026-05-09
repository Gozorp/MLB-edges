"""
sp_savant_gate.py — Strict Statcast NaN/sample handling for SP features (v5.1).

Call gate_sp_features(df) BEFORE score_conviction(). It tags each row with an
sp_savant_status ('OK' | 'SUPPRESS' | 'HARD_VETO') and neutralizes feature
values that fail the gate so downstream conviction logic cannot fire on them.

adjusted_xera_gap(row) returns the reliability-weighted xERA gap that the
v5.1 conviction filter uses for F1-PLATINUM eligibility, replacing the raw
sp_xera_gap that produced the 2026-04-25 BAL 17-1 blowout.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SAVANT_CRITICAL_COLS = ("release_speed", "release_spin_rate", "xwoba_value")
SP_MIN_PITCHES_F1 = 600
SP_MIN_PITCHES_F4 = 800
NAN_FRACTION_LIMIT = 0.05
RELIABILITY_FLOOR_PLAT = 0.50
# Bug-fix 2026-05-08: HARD_VETO threshold for catastrophically thin SP samples.
# Hunter Greene returned from IL with 10 Statcast pitches YTD on 5/8; that
# produces all NaN sp_*_gap features and the model defaults to the home side.
# Any SP under this threshold makes the entire game's SP signal untrustworthy.
SP_THIN_SAMPLE_THRESHOLD = 100


def _nan_fraction(s: pd.Series) -> float:
    if len(s) == 0:
        return 1.0
    return float(s.isna().mean())


def audit_sp_statcast(pitch_df: pd.DataFrame, pitcher_id: int) -> dict:
    sub = pitch_df.loc[pitch_df["pitcher"] == pitcher_id]
    return {
        "n_pitches": int(len(sub)),
        "nan_fracs": {
            "release_speed": _nan_fraction(
                sub.get("release_speed", pd.Series(dtype=float))
            ),
            "release_spin_rate": _nan_fraction(
                sub.get("release_spin_rate", pd.Series(dtype=float))
            ),
            "xwoba_value": _nan_fraction(
                sub.get(
                    "estimated_woba_using_speedangle", pd.Series(dtype=float)
                )
            ),
        },
    }


def gate_sp_features(
    features_df: pd.DataFrame,
    pitch_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    df = features_df.copy()
    statuses: list[str] = []
    reasons: list[str] = []

    for _, row in df.iterrows():
        flags: list[str] = []

        h_n = row.get("home_sp_n_pitches", np.nan)
        a_n = row.get("away_sp_n_pitches", np.nan)
        if pd.isna(h_n) or pd.isna(a_n):
            flags.append(f"NaN n_pitches (h={h_n}, a={a_n}) -> HARD_VETO")
        elif min(h_n, a_n) < SP_THIN_SAMPLE_THRESHOLD:
            # Bug-fix 2026-05-08: thin-sample veto. An SP with <100 Statcast
            # pitches (e.g. just off the IL) is indistinguishable from no
            # signal at all — sp_*_gap features go NaN and the model defaults
            # to the other side. Veto outright. See HOU @ CIN 5/8 (Greene 10p).
            flags.append(
                f"sp_savant_gate=THIN_SAMPLE "
                f"(h={int(h_n)}, a={int(a_n)} < {SP_THIN_SAMPLE_THRESHOLD}) -> HARD_VETO"
            )
        elif min(h_n, a_n) < SP_MIN_PITCHES_F1:
            flags.append(
                f"n_pitches<{SP_MIN_PITCHES_F1} "
                f"(h={int(h_n)}, a={int(a_n)}) -> F1 SUPPRESS"
            )

        rel = row.get("sp_sample_reliability", 0.0)
        if pd.notna(rel) and rel < RELIABILITY_FLOOR_PLAT:
            flags.append(
                f"reliability={rel:.2f}<{RELIABILITY_FLOOR_PLAT} -> PLATINUM blocked"
            )

        if pitch_df is not None:
            for sp_id_col, label in (("home_sp_id", "home"), ("away_sp_id", "away")):
                pid = row.get(sp_id_col)
                if pd.isna(pid):
                    flags.append(f"{label}_sp_id NaN -> HARD_VETO")
                    continue
                audit = audit_sp_statcast(pitch_df, int(pid))
                for col in SAVANT_CRITICAL_COLS:
                    frac = audit["nan_fracs"].get(col, 0.0)
                    if frac > NAN_FRACTION_LIMIT:
                        flags.append(
                            f"{label} pitcher {pid}: {col} NaN frac="
                            f"{frac:.1%} > {NAN_FRACTION_LIMIT:.0%} -> SUPPRESS"
                        )

        if pd.isna(row.get("sp_velo_drop_gap", np.nan)):
            flags.append(
                "sp_velo_drop_gap=NaN -> risk flag, demote PLATINUM->GOLD"
            )

        if any("HARD_VETO" in f for f in flags):
            statuses.append("HARD_VETO")
        elif flags:
            statuses.append("SUPPRESS")
        else:
            statuses.append("OK")
        reasons.append(" | ".join(flags))

    df["sp_savant_status"] = statuses
    df["sp_savant_reason"] = reasons

    veto_mask = df["sp_savant_status"] == "HARD_VETO"
    df.loc[veto_mask, "sp_xera_gap"] = 0.0
    if "team_woba_gap" in df.columns:
        df.loc[veto_mask, "team_woba_gap"] = 0.0

    suppress_mask = df["sp_savant_status"] == "SUPPRESS"
    h_below = df["home_sp_n_pitches"].fillna(0) < SP_MIN_PITCHES_F1
    a_below = df["away_sp_n_pitches"].fillna(0) < SP_MIN_PITCHES_F1
    df.loc[suppress_mask & (h_below | a_below), "sp_xera_gap"] = 0.0

    return df


def adjusted_xera_gap(row: pd.Series) -> float:
    """v5.1 reliability-weighted xERA gap. Use in score_conviction() for the
    F1-PLATINUM eligibility check instead of raw sp_xera_gap."""
    raw = row.get("sp_xera_gap", 0.0)
    rel = row.get("sp_sample_reliability", 0.0)
    if pd.isna(raw) or pd.isna(rel):
        return 0.0
    return float(raw) * float(rel)
