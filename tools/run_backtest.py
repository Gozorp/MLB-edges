"""
run_backtest.py
---------------
Historical PnL backtest for mlb_edge. Loads the bt_YYYY.csv files (graded
picks from past walk-forward backtests) and simulates Kelly-compounded
bankroll growth across 2023-2026 using THREE Kelly fractions in parallel
(full-capped, quarter, eighth) so the user can pick a conservatism level
based on their tolerance for drawdown.

What this script does NOT do (Phase 1 scope):
  * It does not re-run model PREDICTIONS on historical data. The model
    probabilities in bt_*.csv come from prior walk-forward training runs
    (versions v8 / v9 / v12-ish) and we trust them as-is. A Phase 2 build
    would retrain the current model on rolling history and replay.
  * It does not simulate parlay grading (PLATINUM-vs-GOLD tier logic).
    The picks in bt_*.csv already passed the tier filter at backtest time.
    What we simulate here is the bankroll trajectory IF you'd staked each
    pick using current Kelly logic instead of the historical stake column.

Outputs:
  docs/data/backtest/<ts>_summary.md      human-readable Markdown report
  docs/data/backtest/<ts>_ledger.csv      per-pick stake/PnL ledger
  docs/data/backtest/equity_curves.csv    bankroll-by-pick for charting
  docs/data/backtest/latest.json          machine-readable summary
                                          (always points at most-recent run)

Usage:
  python tools/run_backtest.py                              # all seasons
  python tools/run_backtest.py --season 2024                # one season
  python tools/run_backtest.py --start-bankroll 5000        # custom start
  python tools/run_backtest.py --files bt_2023.csv bt_2024.csv
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "data" / "backtest"
OUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backtest")


# ---------------------------------------------------------------------------
# Kelly machinery (mirrors mlb_edge/main_predict.py and edge_calculator.py)
# ---------------------------------------------------------------------------
def kelly_curve(p: float, decimal_odds: float, cap_full: float = 0.25
                ) -> tuple[float, float, float]:
    """Return (full_capped, quarter, eighth) Kelly bankroll fractions.

    f* = (b*p - q) / b   where b = decimal_odds - 1, q = 1 - p

    Full Kelly always capped at `cap_full` to absorb model over-confidence
    (full Kelly assumes perfect calibration and goes parabolic on small
    miscalibration). Quarter and eighth are linear scales of the uncapped
    fraction — they're the standard industry hedges against unmeasured
    miscalibration risk.
    """
    if (pd.isna(p) or pd.isna(decimal_odds)
            or decimal_odds <= 1.0 or p <= 0.0 or p >= 1.0):
        return 0.0, 0.0, 0.0
    b = decimal_odds - 1.0
    raw = (b * p - (1.0 - p)) / b
    raw = max(0.0, raw)
    return min(raw, cap_full), 0.25 * raw, 0.125 * raw


# ---------------------------------------------------------------------------
# Bankroll simulator
# ---------------------------------------------------------------------------
def simulate(picks: pd.DataFrame, kelly_col: str,
             start_bankroll: float = 1000.0) -> pd.DataFrame:
    """Simulate bankroll growth across the chronologically-ordered picks
    using the bankroll fraction in `kelly_col`. Stakes compound — each pick
    is sized against the bankroll AT THAT MOMENT, not the starting bankroll.
    """
    bankroll = start_bankroll
    rows = []
    for _, r in picks.iterrows():
        f = float(r[kelly_col]) if pd.notna(r[kelly_col]) else 0.0
        stake = bankroll * f
        won = bool(r["won"])
        if won:
            pnl = stake * (float(r["decimal"]) - 1.0)
        else:
            pnl = -stake
        bankroll = bankroll + pnl
        rows.append({
            "game_date": r["game_date"],
            "season": r.get("season"),
            "game_id": r["game_id"],
            "team": r["team"],
            "tier": r.get("tier"),
            "decimal": r["decimal"],
            "prob": r["prob"],
            "edge_pp": r.get("edge_pp"),
            "kelly_fraction": f,
            "stake": stake,
            "won": won,
            "pnl": pnl,
            "bankroll": bankroll,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Summary statistics for a single equity curve
# ---------------------------------------------------------------------------
def summarize(eq: pd.DataFrame, start_bankroll: float) -> dict:
    """Compute summary stats from an equity DataFrame produced by simulate()."""
    if eq.empty:
        return {
            "n_picks": 0, "n_bets": 0,
            "final_bankroll": start_bankroll, "total_pnl": 0.0,
            "roi_pct": 0.0, "cagr_pct": None,
            "win_rate_pct": None, "n_wins": 0, "n_losses": 0,
            "max_drawdown_pct": 0.0, "max_drawdown_dollars": 0.0,
            "avg_stake_pct": 0.0, "avg_pnl_per_bet": 0.0,
            "sharpe_per_pick": None,
        }
    final = float(eq["bankroll"].iloc[-1])
    total_pnl = final - start_bankroll
    roi = (final - start_bankroll) / start_bankroll * 100.0

    # CAGR — annualize over actual elapsed time
    try:
        dates = pd.to_datetime(eq["game_date"])
        elapsed_days = (dates.iloc[-1] - dates.iloc[0]).days
        if elapsed_days > 0 and final > 0:
            cagr = ((final / start_bankroll) ** (365.25 / elapsed_days) - 1.0) * 100.0
        else:
            cagr = None
    except Exception:
        cagr = None

    # Sample sizes
    n_picks = len(eq)
    bet_mask = eq["stake"] > 0
    n_bets = int(bet_mask.sum())
    won_mask = bet_mask & eq["won"]
    n_wins = int(won_mask.sum())
    n_losses = int((bet_mask & ~eq["won"]).sum())
    win_rate = (n_wins / n_bets * 100.0) if n_bets > 0 else None

    # Drawdown
    running_max = eq["bankroll"].cummax()
    dd_dollars = (eq["bankroll"] - running_max).min()
    dd_pct = ((eq["bankroll"] / running_max - 1.0) * 100.0).min()

    # Per-pick stats
    avg_stake_pct = float((eq.loc[bet_mask, "kelly_fraction"] * 100.0).mean()) if n_bets > 0 else 0.0
    avg_pnl = float(eq.loc[bet_mask, "pnl"].mean()) if n_bets > 0 else 0.0

    # Crude Sharpe-per-pick (mean PnL / std PnL on bets only)
    if n_bets > 1:
        pnls = eq.loc[bet_mask, "pnl"].to_numpy()
        sigma = float(np.std(pnls, ddof=1))
        sharpe = (float(np.mean(pnls)) / sigma) if sigma > 0 else None
    else:
        sharpe = None

    return {
        "n_picks": n_picks, "n_bets": n_bets,
        "final_bankroll": round(final, 2), "total_pnl": round(total_pnl, 2),
        "roi_pct": round(roi, 2),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "win_rate_pct": round(win_rate, 2) if win_rate is not None else None,
        "n_wins": n_wins, "n_losses": n_losses,
        "max_drawdown_pct": round(float(dd_pct), 2),
        "max_drawdown_dollars": round(float(dd_dollars), 2),
        "avg_stake_pct": round(avg_stake_pct, 3),
        "avg_pnl_per_bet": round(avg_pnl, 3),
        "sharpe_per_pick": round(sharpe, 3) if sharpe is not None else None,
    }


def per_tier_breakdown(eq: pd.DataFrame) -> pd.DataFrame:
    """Hit rate and PnL grouped by tier."""
    if eq.empty:
        return pd.DataFrame()
    bet = eq[eq["stake"] > 0].copy()
    if bet.empty:
        return pd.DataFrame()
    g = bet.groupby("tier", dropna=False).agg(
        n_bets=("won", "size"),
        n_wins=("won", "sum"),
        total_pnl=("pnl", "sum"),
        avg_stake=("stake", "mean"),
        avg_edge_pp=("edge_pp", "mean"),
    ).reset_index()
    g["win_rate_pct"] = (g["n_wins"] / g["n_bets"] * 100.0).round(2)
    g["roi_per_bet"] = (g["total_pnl"] / g["n_bets"]).round(3)
    return g[["tier", "n_bets", "n_wins", "win_rate_pct",
              "total_pnl", "avg_stake", "avg_edge_pp", "roi_per_bet"]]


def ascii_equity_curve(eq: pd.DataFrame, width: int = 60, height: int = 12) -> str:
    """Render a tiny ASCII equity-curve chart for the markdown report.
    Not pretty but works in any terminal and doesn't pull matplotlib."""
    if eq.empty:
        return "(no picks)"
    bankroll = eq["bankroll"].to_numpy()
    if len(bankroll) < 2:
        return "(too few picks for chart)"
    lo, hi = bankroll.min(), bankroll.max()
    if hi == lo:
        return f"  flat at {hi:.2f}"

    # Down-sample to `width` columns
    idx = np.linspace(0, len(bankroll) - 1, width).astype(int)
    sampled = bankroll[idx]
    rows = []
    for level in range(height, 0, -1):
        threshold = lo + (hi - lo) * (level / height)
        line = "".join("█" if v >= threshold else " " for v in sampled)
        rows.append(f"  {threshold:>8.0f} | {line}")
    rows.append(f"  {'':>8} +" + "-" * width)
    rows.append(f"  start={bankroll[0]:.0f}  end={bankroll[-1]:.0f}  "
                f"min={lo:.0f}  max={hi:.0f}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def load_picks(files: List[Path]) -> pd.DataFrame:
    """Load + concatenate bt_*.csv files. Adds a `season` column."""
    frames = []
    for fp in files:
        if not fp.exists():
            log.warning("skip missing file: %s", fp)
            continue
        df = pd.read_csv(fp)
        df["season"] = fp.stem.replace("bt_", "")
        log.info("loaded %s (%d picks)", fp.name, len(df))
        frames.append(df)
    if not frames:
        raise SystemExit("No backtest files loaded.")
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["prob", "decimal", "won"])
    out["won"] = out["won"].astype(bool)
    out = out.sort_values(["game_date", "game_id"]).reset_index(drop=True)
    return out


def build_report(picks: pd.DataFrame, start_bankroll: float,
                 ts: str) -> str:
    """Build the full markdown report. Returns the report text."""
    # Compute Kelly fractions per pick
    kf = picks.apply(
        lambda r: pd.Series(
            kelly_curve(r["prob"], r["decimal"]),
            index=["kelly_full", "kelly_quarter", "kelly_eighth"]),
        axis=1)
    picks = pd.concat([picks, kf], axis=1)

    # Simulate each Kelly variant
    eqs = {}
    summaries = {}
    for col in ["kelly_full", "kelly_quarter", "kelly_eighth"]:
        eqs[col] = simulate(picks, col, start_bankroll=start_bankroll)
        summaries[col] = summarize(eqs[col], start_bankroll)

    # Per-season breakdown using quarter Kelly (the recommended variant)
    season_stats = {}
    season_eqs = {}
    for season, sub in picks.groupby("season"):
        season_eq = simulate(sub, "kelly_quarter", start_bankroll=start_bankroll)
        season_stats[season] = summarize(season_eq, start_bankroll)
        season_eqs[season] = season_eq

    # Tier breakdown (across all seasons, quarter Kelly)
    tier_df = per_tier_breakdown(eqs["kelly_quarter"])

    # ----- write the markdown -----
    parts = []
    parts.append(f"# mlb_edge Historical PnL Backtest\n")
    parts.append(f"_Generated {ts} UTC_\n")
    parts.append(f"Starting bankroll: **\\${start_bankroll:,.0f}**.  "
                 f"Total picks: **{len(picks)}** across "
                 f"{picks['season'].nunique()} season(s).\n")
    parts.append("\n## Headline results — three Kelly fractions, same picks\n")
    parts.append("| Kelly variant | Final bankroll | ROI | CAGR | Win rate | Max DD | Sharpe/pick |\n"
                 "|---|---:|---:|---:|---:|---:|---:|")
    for variant, label in [("kelly_full", "Full (capped 0.25)"),
                           ("kelly_quarter", "Quarter (recommended)"),
                           ("kelly_eighth", "Eighth (conservative)")]:
        s = summaries[variant]
        parts.append(
            f"| {label} | ${s['final_bankroll']:,.0f} "
            f"| {s['roi_pct']:+.1f}% "
            f"| {s['cagr_pct'] if s['cagr_pct'] is not None else 'n/a'}{'%' if s['cagr_pct'] is not None else ''} "
            f"| {s['win_rate_pct']}% "
            f"| {s['max_drawdown_pct']}% "
            f"| {s['sharpe_per_pick']} |")
    parts.append("")
    parts.append("Quarter Kelly is the production recommendation. Full Kelly "
                 "shows ceiling; eighth Kelly shows the floor on a very "
                 "risk-averse policy.\n")

    parts.append("\n## Per-season breakdown (quarter Kelly)\n")
    parts.append("| Season | n picks | Win rate | Final bankroll | ROI | Max DD |\n"
                 "|---|---:|---:|---:|---:|---:|")
    for season in sorted(season_stats):
        s = season_stats[season]
        parts.append(
            f"| {season} | {s['n_picks']} "
            f"| {s['win_rate_pct']}% "
            f"| ${s['final_bankroll']:,.0f} "
            f"| {s['roi_pct']:+.1f}% "
            f"| {s['max_drawdown_pct']}% |")
    parts.append("")

    if not tier_df.empty:
        parts.append("\n## Per-tier breakdown (quarter Kelly, all seasons)\n")
        parts.append("| Tier | n bets | Win rate | Avg edge | Avg stake | Total PnL | PnL/bet |\n"
                     "|---|---:|---:|---:|---:|---:|---:|")
        for _, row in tier_df.iterrows():
            parts.append(
                f"| {row['tier']} | {int(row['n_bets'])} "
                f"| {row['win_rate_pct']}% "
                f"| {row['avg_edge_pp']:+.2f}pp "
                f"| ${row['avg_stake']:.2f} "
                f"| ${row['total_pnl']:+,.2f} "
                f"| ${row['roi_per_bet']:+.3f} |")
        parts.append("")

    parts.append("\n## Equity curve (quarter Kelly, full timeline)\n")
    parts.append("```\n" + ascii_equity_curve(eqs["kelly_quarter"]) + "\n```\n")

    parts.append("\n## Methodology + caveats\n")
    parts.append(
        "- **Compounding model:** each pick is staked as a fraction of the "
        "bankroll AT THE TIME OF THE BET, not the starting bankroll. Wins "
        "and losses fold back into the next bet's stake.\n"
        "- **Kelly fractions:** computed fresh from each pick's `prob` and "
        "`decimal` columns via `(b*p - q)/b`. The historical `stake` column "
        "in bt_*.csv (from prior backtest runs) is **ignored**; this lets us "
        "compare apples-to-apples how current Kelly logic would have sized "
        "those same picks.\n"
        "- **Predictions are NOT re-run.** The probabilities in bt_*.csv come "
        "from prior walk-forward training runs at the time those backtests "
        "were generated. A Phase 2 backtest would retrain the current model "
        "on rolling history and replay — that's a substantially bigger build.\n"
        "- **Picks already pre-filtered.** bt_*.csv only contains picks that "
        "passed the tier filter at backtest time. This isn't a backtest of "
        "the FILTER itself; it's a backtest of the bets the filter let "
        "through, sized with current Kelly.\n"
        "- **Sample sizes are small.** 2025 carries 46 picks (partial-season "
        "data); 2026 only 4 picks. Treat 2023 and 2024 as the meaningful "
        "windows; 2025/2026 are forward-looking.\n"
        "- **No commission, no line shopping, no slippage.** The decimal "
        "odds shown are the odds available at the time the prediction was "
        "logged. In live betting you may get worse fills.\n")
    return "\n".join(parts), eqs, summaries, season_stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default=None,
                    help="Restrict to a single season (e.g. 2024).")
    ap.add_argument("--start-bankroll", type=float, default=1000.0)
    ap.add_argument("--files", nargs="+", default=None,
                    help="Override default bt_*.csv list.")
    args = ap.parse_args(argv)

    if args.files:
        files = [ROOT / f for f in args.files]
    elif args.season:
        files = [ROOT / f"bt_{args.season}.csv"]
    else:
        files = [ROOT / f for f in ("bt_2023.csv", "bt_2024.csv",
                                    "bt_2025.csv", "bt_2026.csv")]

    picks = load_picks(files)
    log.info("simulating bankroll across %d picks", len(picks))

    ts = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")
    report_md, eqs, summaries, season_stats = build_report(
        picks, start_bankroll=args.start_bankroll, ts=ts)

    summary_path = OUT_DIR / f"{ts}_summary.md"
    summary_path.write_text(report_md, encoding="utf-8")
    log.info("wrote %s", summary_path)

    # Per-pick ledger (quarter Kelly recommended view)
    ledger_path = OUT_DIR / f"{ts}_ledger.csv"
    eqs["kelly_quarter"].to_csv(ledger_path, index=False)
    log.info("wrote %s", ledger_path)

    # Equity curves for all three variants (dashboard charting)
    eq_cols = []
    for variant in ["kelly_full", "kelly_quarter", "kelly_eighth"]:
        e = eqs[variant][["game_date", "game_id", "bankroll"]].copy()
        e["variant"] = variant
        eq_cols.append(e)
    eq_combined = pd.concat(eq_cols, ignore_index=True)
    eq_path = OUT_DIR / "equity_curves.csv"
    eq_combined.to_csv(eq_path, index=False)
    log.info("wrote %s", eq_path)

    # latest.json — machine-readable summary for cron consumers
    latest = {
        "generated_at": ts,
        "n_picks": len(picks),
        "start_bankroll": args.start_bankroll,
        "summaries": summaries,
        "per_season": season_stats,
    }
    (OUT_DIR / "latest.json").write_text(
        json.dumps(latest, indent=2, default=str), encoding="utf-8")
    log.info("wrote %s", OUT_DIR / "latest.json")

    # Echo the markdown to stdout so cron logs surface it
    print(report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
