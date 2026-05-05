"""Daily eval: compare production p_model vs shadow p_model_shadow_phase4.

Reads picks_<date>_diag.csv, joins on actual game outcomes from the MLB
Stats API, and appends one row per day to D:\\mlb_edge\\phase4_shadow_log.csv.

Schema of phase4_shadow_log.csv:
    eval_date              # date the cron ran
    slate_date             # the slate evaluated
    n_games                # games on the slate
    n_outcomes             # games with final outcomes (= n_games unless rainout)
    n_shadow_present       # rows where p_model_shadow_phase4 was non-NaN

    # Production model
    prod_brier             # pooled Brier of production p_model on pick perspective
    prod_log_loss
    prod_hit_rate          # picks at >=0.5 confidence (= every row, by construction)
    prod_hit_rate_conv     # hit rate among GOLD/PLATINUM/DIAMOND tier rows only

    # Shadow model
    shadow_brier
    shadow_log_loss
    shadow_hit_rate
    shadow_hit_rate_conv

    # Deltas (shadow - prod, lower-is-better convention)
    delta_brier
    delta_log_loss
    delta_hit_rate         # shadow - prod (positive = shadow better)
    delta_hit_rate_conv

    # Bootstrap CI on Brier delta (n_resamples=500). NaN if n_games < 5.
    boot_ci_lo
    boot_ci_hi
    boot_ci_excludes_zero  # 1 if (lo>0 OR hi<0) else 0

    # Archetype counter — games where production model was high-confidence
    # AND one side had thin bullpen sample. The cards shadow was designed
    # to fix.
    n_archetype            # rows with p_model >= 0.78 AND bp_min < 1500
    archetype_brier_prod
    archetype_brier_shadow

Run nightly via task scheduler / cron after games are final.
Idempotent: re-running for the same slate_date overwrites that row.
"""
from __future__ import annotations
import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge.auto_weight_update import fetch_outcomes
from mlb_edge.config import BAYESIAN_SHRINKAGE_CFG

LOG_PATH = Path(BAYESIAN_SHRINKAGE_CFG["shadow_log_path"])
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("phase4_shadow")


def load_diag(slate_date: date) -> pd.DataFrame:
    p = Path(rf"D:\mlb_edge\mlb_edge\picks_{slate_date.isoformat()}_diag.csv")
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


def join_outcomes(diag: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    """Join diag (matchup="AWAY @ HOME", pick=ABBR) with outcomes
    (home_abbr, away_abbr, home_R, away_R)."""
    out = outcomes.copy()
    if out.empty or diag.empty:
        return pd.DataFrame()
    out["matchup"] = out["away_abbr"] + " @ " + out["home_abbr"]
    out["home_won"] = (out["home_R"] > out["away_R"]).astype(int)
    out["away_won"] = (out["away_R"] > out["home_R"]).astype(int)
    merged = diag.merge(
        out[["matchup", "home_abbr", "away_abbr", "home_R", "away_R",
             "home_won", "away_won"]],
        on="matchup", how="inner",
    )
    # Pick-perspective truth: 1 if pick won, 0 if pick lost
    merged["pick_won"] = np.where(
        merged["pick"] == merged["home_abbr"],
        merged["home_won"],
        merged["away_won"],
    ).astype(int)
    return merged


def compute_metrics(merged: pd.DataFrame) -> dict:
    """Compute pooled metrics for production and shadow."""
    if merged.empty:
        return {}
    valid_prod = merged.dropna(subset=["pick_prob", "pick_won"])
    valid_shadow = merged.dropna(subset=["p_model_shadow_phase4", "pick_won"])
    out = {"n_games": len(merged), "n_shadow_present": len(valid_shadow)}

    def metric_set(p, y):
        p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
        y = np.asarray(y, dtype=int)
        return {
            "brier": float(brier_score_loss(y, p)),
            "log_loss": float(log_loss(y, p)),
            "hit_rate": float(((p >= 0.5).astype(int) == y).mean()),
        }

    if len(valid_prod):
        m = metric_set(valid_prod["pick_prob"], valid_prod["pick_won"])
        out.update({f"prod_{k}": v for k, v in m.items()})
    if len(valid_shadow):
        m = metric_set(valid_shadow["p_model_shadow_phase4"], valid_shadow["pick_won"])
        out.update({f"shadow_{k}": v for k, v in m.items()})

    # Conviction-tier-only metrics (rows with tier in GOLD/PLATINUM/DIAMOND)
    conv_mask = merged["tier"].isin(["GOLD", "PLATINUM", "DIAMOND"])
    conv = merged[conv_mask].dropna(subset=["pick_prob", "pick_won"])
    if len(conv):
        m = metric_set(conv["pick_prob"], conv["pick_won"])
        out["prod_brier_conv"] = m["brier"]
        out["prod_hit_rate_conv"] = m["hit_rate"]
    conv_s = merged[conv_mask].dropna(subset=["p_model_shadow_phase4", "pick_won"])
    if len(conv_s):
        m = metric_set(conv_s["p_model_shadow_phase4"], conv_s["pick_won"])
        out["shadow_brier_conv"] = m["brier"]
        out["shadow_hit_rate_conv"] = m["hit_rate"]

    # Deltas
    if "prod_brier" in out and "shadow_brier" in out:
        out["delta_brier"] = out["shadow_brier"] - out["prod_brier"]
        out["delta_log_loss"] = out["shadow_log_loss"] - out["prod_log_loss"]
        out["delta_hit_rate"] = out["shadow_hit_rate"] - out["prod_hit_rate"]
    if "prod_hit_rate_conv" in out and "shadow_hit_rate_conv" in out:
        out["delta_hit_rate_conv"] = out["shadow_hit_rate_conv"] - out["prod_hit_rate_conv"]

    # Bootstrap CI on Brier delta (when both columns present + ≥5 games)
    both = merged.dropna(subset=["pick_prob", "p_model_shadow_phase4", "pick_won"])
    if len(both) >= 5:
        rng = np.random.default_rng(42)
        n = len(both)
        p_p = np.clip(both["pick_prob"].values.astype(float), 1e-6, 1 - 1e-6)
        p_s = np.clip(both["p_model_shadow_phase4"].values.astype(float), 1e-6, 1 - 1e-6)
        y = both["pick_won"].values.astype(int)
        diffs = []
        for _ in range(500):
            idx = rng.integers(0, n, size=n)
            d = brier_score_loss(y[idx], p_s[idx]) - brier_score_loss(y[idx], p_p[idx])
            diffs.append(d)
        diffs = np.array(diffs)
        lo, hi = float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))
        out["boot_ci_lo"] = lo
        out["boot_ci_hi"] = hi
        out["boot_ci_excludes_zero"] = int(lo > 0 or hi < 0)

    return out


def archetype_metrics(merged: pd.DataFrame) -> dict:
    """Find rows where p_model >= 0.78 AND bp_min < 1500. Compare prod vs
    shadow on this subset. `bp_min` is captured at predict time and lives
    in the diag CSV directly."""
    out = {"n_archetype": 0}
    if merged.empty or "bp_min" not in merged.columns:
        return out
    bpm = pd.to_numeric(merged["bp_min"], errors="coerce")
    arch = merged[(merged["pick_prob"] >= 0.78) & (bpm < 1500)]
    out["n_archetype"] = len(arch)
    if len(arch) >= 1:
        y = arch["pick_won"].values.astype(int)
        out["archetype_brier_prod"] = float(
            brier_score_loss(y, np.clip(arch["pick_prob"].values, 1e-6, 1 - 1e-6))
        )
        valid_s = arch.dropna(subset=["p_model_shadow_phase4"])
        if len(valid_s):
            out["archetype_brier_shadow"] = float(
                brier_score_loss(valid_s["pick_won"].values.astype(int),
                                 np.clip(valid_s["p_model_shadow_phase4"].values, 1e-6, 1 - 1e-6))
            )
    return out


def append_log(row: dict):
    """Idempotent append: if a row with the same slate_date exists, replace it."""
    cols_order = [
        "eval_date", "slate_date", "n_games", "n_outcomes", "n_shadow_present",
        "prod_brier", "prod_log_loss", "prod_hit_rate",
        "prod_brier_conv", "prod_hit_rate_conv",
        "shadow_brier", "shadow_log_loss", "shadow_hit_rate",
        "shadow_brier_conv", "shadow_hit_rate_conv",
        "delta_brier", "delta_log_loss", "delta_hit_rate", "delta_hit_rate_conv",
        "boot_ci_lo", "boot_ci_hi", "boot_ci_excludes_zero",
        "n_archetype", "archetype_brier_prod", "archetype_brier_shadow",
    ]
    if LOG_PATH.exists():
        df = pd.read_csv(LOG_PATH)
        df = df[df["slate_date"] != row["slate_date"]]
    else:
        df = pd.DataFrame(columns=cols_order)
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    # Reorder columns, keep any new ones we added
    for c in cols_order:
        if c not in df.columns:
            df[c] = None
    extra = [c for c in df.columns if c not in cols_order]
    df = df[cols_order + extra]
    df = df.sort_values("slate_date")
    df.to_csv(LOG_PATH, index=False)
    log.info("Appended row to %s (now %d entries)", LOG_PATH, len(df))


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                        default=date.today() - timedelta(days=1),
                        help="Slate date to evaluate (default: yesterday)")
    args = parser.parse_args()
    slate_date = args.date

    log.info("Phase 4 shadow eval for slate %s", slate_date)

    diag = load_diag(slate_date)
    if diag.empty:
        log.warning("No diag CSV for %s, skipping", slate_date)
        return

    if "p_model_shadow_phase4" not in diag.columns:
        log.warning("Diag CSV for %s has no shadow column (predict ran before "
                    "shadow was wired). Skipping.", slate_date)
        return

    outcomes = fetch_outcomes(slate_date)
    if outcomes.empty:
        log.warning("No outcomes for %s yet; eval would be incomplete. Try "
                    "again later tonight.", slate_date)
        return

    merged = join_outcomes(diag, outcomes)
    if merged.empty:
        log.warning("Outcome join produced 0 rows for %s — schema/match issue?",
                    slate_date)
        return

    metrics = compute_metrics(merged)
    arch = archetype_metrics(merged)
    metrics.update(arch)
    metrics["eval_date"] = date.today().isoformat()
    metrics["slate_date"] = slate_date.isoformat()
    metrics["n_outcomes"] = int(len(outcomes))

    log.info("Slate %s: n_games=%d, n_shadow=%d, n_archetype=%d",
             slate_date, metrics.get("n_games", 0),
             metrics.get("n_shadow_present", 0),
             metrics.get("n_archetype", 0))
    if "delta_brier" in metrics:
        log.info("  prod_brier=%.4f shadow_brier=%.4f delta=%+.4f  "
                 "prod_hit=%.3f shadow_hit=%.3f delta=%+.3f",
                 metrics["prod_brier"], metrics["shadow_brier"], metrics["delta_brier"],
                 metrics["prod_hit_rate"], metrics["shadow_hit_rate"], metrics["delta_hit_rate"])
    if "boot_ci_excludes_zero" in metrics:
        log.info("  bootstrap CI95 [%+.4f, %+.4f]  excludes_zero=%d",
                 metrics["boot_ci_lo"], metrics["boot_ci_hi"],
                 metrics["boot_ci_excludes_zero"])

    append_log(metrics)


if __name__ == "__main__":
    main()
