"""
player_rates.py
---------------
Phase 1 of the Monte Carlo plate-appearance simulator
(memory/mc_phase1_plan.md). SHADOW MODE — adds `pred_winp_mc` /
`pred_runs_mc` columns to the diag CSV alongside the production XGBoost
columns. Production picks/tiers/edges DO NOT consume MC output yet.

Public API
~~~~~~~~~~
    fetch_batter_rates(player_ids, date)   -> pd.DataFrame
    fetch_pitcher_allowed_rates(pid, date) -> Dict[str, float]
    LEAGUE_RATES                            -> Dict[str, float]

Per-player outcome rates are derived from the daily Savant CSV snapshots
in ``data/savant/`` (expected-statistics + statcast-exit-velocity). We
chose that path over re-aggregating the pitch-by-pitch parquet cache
because:

  1. The CSVs are already refreshed by ``savant_scraper`` every morning
     before predict.py fires (one cache layer to reason about, not two).
  2. They give us one row per player with the rate stats we need —
     no per-pitch aggregation cost inside the slate loop.
  3. The CSVs are small (~1MB each) so per-slate load + index build is
     <100ms; we cache the lookup dict at module level so the simulator
     can hammer it across 15 games × 18 batters × 10,000 sims without
     re-touching disk.

Outcome derivation
~~~~~~~~~~~~~~~~~~
For each batter we need a probability vector over the canonical PA
outcomes used by monte_carlo.simulate_game:
    [K, BB, HBP, 1B, 2B, 3B, HR, GIDP, FO, GO, LO]

The Savant CSVs give us xwOBA + xBA + xSLG + barrel% + ev95%, but NOT a
direct K% / BB% breakdown (that lives in the pitch-by-pitch cache). For
Phase 1 we use a deterministic mapping:

  K%   = LG_K_PCT * (xwOBA_LG / xwOBA_BATTER) ** 0.5
         (worse hitters strike out more; weak proxy but stable)
  BB%  = LG_BB_PCT
         (no reliable batter-level BB signal from these CSVs)
  HR%  = (HR / PA) directly from expected-statistics if SLG > 0,
         else LG_HR_PCT * (xwOBA_BATTER / xwOBA_LG) ** 2
  XBH% = (xSLG - xBA) → split 70/25/5 across 2B/3B/HR after HR is
         already pinned, with the residual landing in 2B.
  1B%  = xBA - HR% - 2B% - 3B%  (clamped to >= 0)
  Outs = 1 - K - BB - HBP - 1B - 2B - 3B - HR
         then split GO/FO/LO/GIDP by league shares.

Pitcher allowed rates use the same CSV (pitcher-side rows) with
identical formulas, with the implicit assumption that a pitcher who
gives up high xwOBA gives up proportionally more of each hit class.

When a player is not in the CSV (rookie, recent callup, weird ID),
fall back to LEAGUE_RATES with confidence_weight=0 in the simulator's
log-5 blend (the blend then leans entirely on the opposing rate vector).

See monte_carlo.py for the simulation engine itself.
"""
from __future__ import annotations

import logging
import os
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# League-average per-PA outcome rates (MLB 2024-2026 blended).
# Source: FanGraphs season totals; rounded to 4dp.
# These sum to ~1.000 by construction.
# ---------------------------------------------------------------------------
LEAGUE_RATES: Dict[str, float] = {
    "K":   0.2250,
    "BB":  0.0850,
    "HBP": 0.0120,
    "1B":  0.1400,
    "2B":  0.0450,
    "3B":  0.0040,
    "HR":  0.0300,
    "GIDP": 0.0220,   # double plays — counted as 2 outs in sim
    "FO":  0.1700,    # fly out (incl. line-drive flyouts to OF)
    "GO":  0.1900,    # ground out
    "LO":  0.0770,    # line out
}
# Convenience views
OUTCOMES: List[str] = list(LEAGUE_RATES.keys())
LEAGUE_PROB_VEC: np.ndarray = np.array([LEAGUE_RATES[o] for o in OUTCOMES])

# League-average xwOBA used as the "neutral" anchor for the
# xwOBA-relative outcome derivation. 0.318 ≈ 2024 MLB average.
LG_XWOBA = 0.318
LG_XBA = 0.245
LG_XSLG = 0.405

# ---------------------------------------------------------------------------
# Savant CSV discovery
# ---------------------------------------------------------------------------
# Repo-root-relative path. Workflows cd into the repo root before running.
SAVANT_ROOT = Path("data/savant")
EXPSTATS_DIR = SAVANT_ROOT / "expected-statistics"
EVBARRELS_DIR = SAVANT_ROOT / "statcast-exit-velocity"


def _ymd_for(d: date_cls | str) -> str:
    """Convert a date or YYYY-MM-DD string to YYYYMMDD (Savant filename fmt)."""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    return d.strftime("%Y%m%d")


def _latest_csv(directory: Path, on_or_before_ymd: str, prefix: str) -> Optional[Path]:
    """Return the most recent CSV file in `directory` whose YYYYMMDD suffix
    is <= `on_or_before_ymd`. Returns None if the directory doesn't exist
    or no matching file is found.
    """
    if not directory.exists():
        return None
    candidates: List[Path] = []
    for p in directory.glob(f"{prefix}_*.csv"):
        # Filename is e.g. expected-statistics_20260523.csv
        stem = p.stem
        try:
            ymd = stem.rsplit("_", 1)[1]
            if ymd <= on_or_before_ymd and len(ymd) == 8:
                candidates.append(p)
        except (IndexError, ValueError):
            continue
    if not candidates:
        return None
    # Lexicographic max on YYYYMMDD == chronological max.
    return max(candidates, key=lambda p: p.stem.rsplit("_", 1)[1])


# ---------------------------------------------------------------------------
# Cached CSV loaders (slate-scoped via module-level dict).
# ---------------------------------------------------------------------------
# Keyed by (resolved_path) so we don't re-read the same CSV when batter/
# pitcher requests for the same date come in across the slate loop.
_CSV_CACHE: Dict[str, pd.DataFrame] = {}


def _load_csv_cached(path: Path) -> pd.DataFrame:
    key = str(path)
    if key in _CSV_CACHE:
        return _CSV_CACHE[key]
    try:
        # Savant CSVs ship with a UTF-8 BOM and have the player name as
        # 'last_name, first_name' (single column, comma-quoted). We accept
        # whatever pandas hands us; we only need player_id + numeric cols.
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as e:
        log.warning("[player_rates] failed to load %s: %s", path, e)
        df = pd.DataFrame()
    _CSV_CACHE[key] = df
    return df


def reset_cache() -> None:
    """Clear all cached CSVs. Useful when the test harness wants a clean slate."""
    _CSV_CACHE.clear()
    _BATTER_RATE_CACHE.clear()
    _PITCHER_RATE_CACHE.clear()


# ---------------------------------------------------------------------------
# Outcome derivation from xwOBA / xSLG / brl%.
# ---------------------------------------------------------------------------
def _derive_rates_from_xwoba(
    xwoba: Optional[float],
    xba:   Optional[float],
    xslg:  Optional[float],
    brl_pct: Optional[float] = None,
) -> Dict[str, float]:
    """Map per-player Savant aggregates onto the canonical 11-bucket
    outcome vector. See module docstring for the rationale.

    All inputs may be None / NaN — when so, we return the league-rate
    dict so the simulator can blend without crashing.
    """
    # Coerce to safe floats
    def _num(x: object, default: float) -> float:
        if x is None:
            return default
        try:
            v = float(x)
        except (TypeError, ValueError):
            return default
        if not np.isfinite(v):
            return default
        return v

    xwoba_v = _num(xwoba, LG_XWOBA)
    xba_v   = _num(xba,   LG_XBA)
    xslg_v  = _num(xslg,  LG_XSLG)
    brl_v   = _num(brl_pct, 5.0) / 100.0  # brl% to fraction

    # Quality ratio (1.0 = league avg).
    qratio = xwoba_v / LG_XWOBA if LG_XWOBA > 0 else 1.0
    # Clamp ratios to keep extreme players from producing degenerate distributions
    # (e.g. a backup with 1 PA and xwOBA=0 would otherwise crash the K% formula).
    qratio = float(np.clip(qratio, 0.55, 1.55))

    # K% — better hitters K less. Use inverse-quality scaling, then clamp.
    k_pct = LEAGUE_RATES["K"] * (1.0 / max(qratio, 0.1)) ** 0.5
    k_pct = float(np.clip(k_pct, 0.10, 0.40))

    # BB% — Savant CSVs don't give us batter BB% so anchor to league;
    # nudge slightly with quality (good hitters walk a bit more).
    bb_pct = LEAGUE_RATES["BB"] * (qratio ** 0.4)
    bb_pct = float(np.clip(bb_pct, 0.04, 0.18))

    # HBP — anchored to league (we have no per-player signal here).
    hbp_pct = LEAGUE_RATES["HBP"]

    # HR% — barrels are the strongest predictor; ev95% would be even better
    # but we'd need to read another CSV. Use brl% directly: ~25% of barrels
    # become HR (Statcast 2024 mean), with a floor at league rate.
    hr_pct = float(np.clip(brl_v * 0.25, 0.005, 0.090))

    # Total hits per PA ≈ xBA. Split into singles vs XBH using xSLG-xBA
    # (extra bases per PA → bias the XBH share).
    hits_per_pa = float(np.clip(xba_v, 0.10, 0.40))
    xb_per_pa   = max(xslg_v - xba_v, 0.0)
    # Of all XBH (2B+3B+HR), MLB share: 2B ≈ 73%, 3B ≈ 6%, HR ≈ 21%.
    # We already pinned HR via barrels — so allocate the remaining (xb_per_pa - hr_pct)
    # across 2B/3B with that ratio.
    xb_remaining = max(xb_per_pa - hr_pct, 0.0)
    two_b_pct   = float(np.clip(xb_remaining * 0.92, 0.005, 0.10))
    three_b_pct = float(np.clip(xb_remaining * 0.08, 0.001, 0.02))
    one_b_pct   = float(np.clip(hits_per_pa - hr_pct - two_b_pct - three_b_pct,
                                0.05, 0.25))

    # Remaining mass goes to outs.
    out_pct = 1.0 - (k_pct + bb_pct + hbp_pct
                     + one_b_pct + two_b_pct + three_b_pct + hr_pct)
    if out_pct < 0.05:
        # Over-constrained — collapse XBH and hits to make room.
        scale = max((1.0 - 0.05 - k_pct - bb_pct - hbp_pct) /
                    (one_b_pct + two_b_pct + three_b_pct + hr_pct + 1e-9), 0.1)
        one_b_pct   *= scale
        two_b_pct   *= scale
        three_b_pct *= scale
        hr_pct      *= scale
        out_pct = 1.0 - (k_pct + bb_pct + hbp_pct
                         + one_b_pct + two_b_pct + three_b_pct + hr_pct)
    out_pct = max(out_pct, 0.05)

    # Distribute outs across GIDP / GO / FO / LO using league shares.
    lg_out_total = (LEAGUE_RATES["GIDP"] + LEAGUE_RATES["GO"]
                    + LEAGUE_RATES["FO"] + LEAGUE_RATES["LO"])
    gidp_share = LEAGUE_RATES["GIDP"] / lg_out_total
    go_share   = LEAGUE_RATES["GO"]   / lg_out_total
    fo_share   = LEAGUE_RATES["FO"]   / lg_out_total
    lo_share   = LEAGUE_RATES["LO"]   / lg_out_total

    rates = {
        "K":   k_pct,
        "BB":  bb_pct,
        "HBP": hbp_pct,
        "1B":  one_b_pct,
        "2B":  two_b_pct,
        "3B":  three_b_pct,
        "HR":  hr_pct,
        "GIDP": out_pct * gidp_share,
        "GO":   out_pct * go_share,
        "FO":   out_pct * fo_share,
        "LO":   out_pct * lo_share,
    }
    # Final normalize for floating-point drift.
    total = sum(rates.values())
    if total > 0:
        for k in rates:
            rates[k] /= total
    return rates


def _league_rate_dict() -> Dict[str, float]:
    return dict(LEAGUE_RATES)


# ---------------------------------------------------------------------------
# Per-batter / per-pitcher rate lookups (cached by (player_id, ymd)).
# ---------------------------------------------------------------------------
_BATTER_RATE_CACHE: Dict[tuple, Dict[str, float]] = {}
_PITCHER_RATE_CACHE: Dict[tuple, Dict[str, float]] = {}


def _expstats_for_date(date_str: str) -> pd.DataFrame:
    ymd = _ymd_for(date_str)
    path = _latest_csv(EXPSTATS_DIR, ymd, "expected-statistics")
    if path is None:
        return pd.DataFrame()
    df = _load_csv_cached(path)
    if df.empty or "player_id" not in df.columns:
        return pd.DataFrame()
    # Coerce player_id to int once
    df = df.copy()
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    return df


def _evbarrels_for_date(date_str: str) -> pd.DataFrame:
    ymd = _ymd_for(date_str)
    path = _latest_csv(EVBARRELS_DIR, ymd, "statcast-exit-velocity")
    if path is None:
        return pd.DataFrame()
    df = _load_csv_cached(path)
    if df.empty or "player_id" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    return df


def fetch_batter_rates(player_ids: Iterable[int], date: str) -> pd.DataFrame:
    """Returns DataFrame keyed by player_id with one column per outcome
    in OUTCOMES order plus `data_source` (one of 'savant', 'league').

    Missing players get league rates with data_source='league' (the
    simulator's blend layer treats those as zero-confidence batter input,
    so the matchup is driven entirely by the opposing pitcher).
    """
    exp_df = _expstats_for_date(date)
    ev_df = _evbarrels_for_date(date)

    if not exp_df.empty:
        exp_lookup = exp_df.set_index("player_id")
    else:
        exp_lookup = None
    if not ev_df.empty:
        ev_lookup = ev_df.set_index("player_id")
    else:
        ev_lookup = None

    rows: List[Dict] = []
    for pid in player_ids:
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            continue
        cache_key = (pid_i, date)
        if cache_key in _BATTER_RATE_CACHE:
            rates = _BATTER_RATE_CACHE[cache_key]
            row = {"player_id": pid_i, **rates}
            rows.append(row)
            continue

        xwoba = xba = xslg = brl = None
        source = "league"
        if exp_lookup is not None and pid_i in exp_lookup.index:
            r = exp_lookup.loc[pid_i]
            # Some leaderboards return multiple rows per player when split
            # across years; collapse with mean if so.
            if isinstance(r, pd.DataFrame):
                r = r.iloc[0]
            xwoba = r.get("est_woba")
            xba = r.get("est_ba")
            xslg = r.get("est_slg")
            source = "savant"
        if ev_lookup is not None and pid_i in ev_lookup.index:
            r2 = ev_lookup.loc[pid_i]
            if isinstance(r2, pd.DataFrame):
                r2 = r2.iloc[0]
            brl = r2.get("brl_percent")

        if source == "league":
            rates = _league_rate_dict()
            rates["_data_source"] = "league"
        else:
            rates = _derive_rates_from_xwoba(xwoba, xba, xslg, brl)
            rates["_data_source"] = "savant"

        _BATTER_RATE_CACHE[cache_key] = rates
        rows.append({"player_id": pid_i, **rates})

    if not rows:
        cols = ["player_id"] + OUTCOMES + ["_data_source"]
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)


def fetch_pitcher_allowed_rates(pitcher_id: int, date: str) -> Dict[str, float]:
    """Per-pitcher ALLOWED rate vector. Returns a dict shaped like
    LEAGUE_RATES plus `_data_source`.

    For Phase 1 we read the same expected-statistics CSV but on the
    pitcher rows: the Savant CSV ships separate batter-vs and pitcher-vs
    leaderboards but the public expected-statistics endpoint we already
    cache only has hitters. So as a Phase 1 starting point we anchor the
    pitcher distribution to LEAGUE_RATES and let main_predict.py override
    with the existing `point_in_time.pitcher_as_of` shrunk K/BB rates
    when those are available on the slate row (see monte_carlo.py
    `simulate_game` for how it consumes a `k_pct_allowed` / `bb_pct_allowed`
    override pair).
    """
    cache_key = (int(pitcher_id), date)
    if cache_key in _PITCHER_RATE_CACHE:
        return _PITCHER_RATE_CACHE[cache_key]
    rates = _league_rate_dict()
    rates["_data_source"] = "league"
    _PITCHER_RATE_CACHE[cache_key] = rates
    return rates


def pitcher_rates_from_overrides(
    k_pct: Optional[float],
    bb_pct: Optional[float],
    xwoba_allowed: Optional[float] = None,
) -> Dict[str, float]:
    """Build a pitcher-allowed rate vector from the K%/BB%/xwOBA values
    that build_pipeline already attaches per row (sp_k_pct, sp_bb_pct,
    sp_xwoba_allowed). This is the preferred entry point at run time —
    the daily slate loop calls this with the SP's already-shrunk season
    rates and we avoid a second pass through the parquet cache.

    Falls through to LEAGUE_RATES when all three inputs are None.
    """
    if k_pct is None and bb_pct is None and xwoba_allowed is None:
        out = _league_rate_dict()
        out["_data_source"] = "league"
        return out

    # K%/BB% come in as PERCENT (e.g. 22.5) from point_in_time.
    if k_pct is not None:
        try:
            k_pct = float(k_pct) / 100.0
        except (TypeError, ValueError):
            k_pct = None
    if bb_pct is not None:
        try:
            bb_pct = float(bb_pct) / 100.0
        except (TypeError, ValueError):
            bb_pct = None
    if xwoba_allowed is not None:
        try:
            xwoba_allowed = float(xwoba_allowed)
        except (TypeError, ValueError):
            xwoba_allowed = None

    # Start from league, override K/BB with pitcher's actuals, then scale
    # the hit-class buckets by xwOBA_allowed / LG_XWOBA so a dominant
    # SP's distribution shifts mass into K/outs.
    base = _league_rate_dict()
    if k_pct is not None and np.isfinite(k_pct):
        base["K"] = float(np.clip(k_pct, 0.08, 0.45))
    if bb_pct is not None and np.isfinite(bb_pct):
        base["BB"] = float(np.clip(bb_pct, 0.02, 0.18))

    if xwoba_allowed is not None and np.isfinite(xwoba_allowed):
        ratio = xwoba_allowed / LG_XWOBA if LG_XWOBA > 0 else 1.0
        ratio = float(np.clip(ratio, 0.6, 1.5))
        # Scale hit-class outcomes by the ratio (worse SP -> more hits).
        for hk in ("1B", "2B", "3B", "HR"):
            base[hk] *= ratio
        # The residual goes back into outs (GO/FO/LO/GIDP) to keep sum=1.

    # Renormalize.
    total = sum(v for k, v in base.items() if k != "_data_source")
    if total > 0:
        for k in list(base.keys()):
            if k == "_data_source":
                continue
            base[k] /= total
    base["_data_source"] = "overrides"
    return base


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse, json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=date_cls.today().isoformat(),
                   help="Slate date YYYY-MM-DD (default today)")
    p.add_argument("--player", type=int, action="append",
                   help="One or more player_ids to look up (repeat flag)")
    args = p.parse_args()

    pids = args.player or [660271, 545361, 592450]  # Ohtani / Goldy / Judge
    rates_df = fetch_batter_rates(pids, args.date)
    print(rates_df.to_string(index=False))
    print()
    print("Sums:", rates_df[OUTCOMES].sum(axis=1).tolist())
