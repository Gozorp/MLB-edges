"""
tools/backtest_bvp_totals.py
----------------------------
Postgame comparison harness for the BvP-adjusted totals shadow.

Walks every ``docs/data/picks_totals_*.csv`` in the repo, joins each row to the
final score for that matchup via the statsapi schedule endpoint, and emits a
markdown report comparing the production ``pred_runs`` prediction against the
shadow ``pred_runs_bvp_adjusted`` prediction.

Promotion gate (Rule 10):
    Promote shadow -> production O/U pick when BvP-adjusted RMSE is
    >= 5% lower than raw across at least 7 days (~70 graded games).

Phase-1 ship state:
    Most historical picks CSVs predate the shadow columns landing in
    main_totals.py.  Rows without ``pred_runs_bvp_adjusted`` are scored for
    the raw ``pred_runs`` metric only and counted toward the "INSUFFICIENT
    DATA" pool.  After ~7 daily-slate runs post-Task-1 we'll have a real
    apples-to-apples comparison.

Pure stdlib + urllib so this can ship in the same minimal-deps CI box that
runs ``tools/refit_post_calibrator.py``.

Usage:
    python tools/backtest_bvp_totals.py
    python tools/backtest_bvp_totals.py --out experiments/bvp_backtest_2026-05-21.md
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import logging
import math
import os
import re
import sys
import time
import urllib.request
from datetime import date as _date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("backtest_bvp_totals")

ABBR_FIX = {"CHW": "CWS", "WSH": "WSH", "OAK": "ATH",
            "KCR": "KC", "TBR": "TB", "ARI": "AZ"}

PROMOTION_GATE_RMSE_IMPROVEMENT = 0.05
PROMOTION_GATE_MIN_N = 60


def fetch_results(date_str, retries=3, sleep=0.3):
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={date_str}&hydrate=linescore,team")
    last_err = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                j = json.load(r)
            break
        except Exception as e:
            last_err = e
            time.sleep(sleep)
    else:
        log.warning("results fetch failed for %s: %s", date_str, last_err)
        return {}

    out = {}
    for d in j.get("dates", []):
        for g in d.get("games", []):
            t = g.get("teams", {})
            a = (t.get("away", {}).get("team", {}).get("abbreviation") or "")
            h = (t.get("home", {}).get("team", {}).get("abbreviation") or "")
            a_score = t.get("away", {}).get("score")
            h_score = t.get("home", {}).get("score")
            status = g.get("status", {}).get("detailedState", "")
            out[f"{a}@{h}"] = dict(
                away=a, home=h,
                away_score=a_score, home_score=h_score,
                status=status,
                total=(int(a_score) + int(h_score))
                      if (a_score is not None and h_score is not None) else None,
                is_final=(status in ("Final", "Game Over", "Completed Early")),
            )
    return out


def lookup_result(date_results, away, home):
    candidates = [
        f"{away}@{home}",
        f"{ABBR_FIX.get(away, away)}@{ABBR_FIX.get(home, home)}",
        f"{away}@{ABBR_FIX.get(home, home)}",
        f"{ABBR_FIX.get(away, away)}@{home}",
    ]
    for k in candidates:
        if k in date_results:
            return date_results[k]
    return None


def _rmse(errs):
    if not errs:
        return None
    return math.sqrt(sum(e * e for e in errs) / len(errs))


def _mae(errs):
    if not errs:
        return None
    return sum(abs(e) for e in errs) / len(errs)


def _ou_pick(pred, line):
    if pred > line:
        return "over"
    if pred < line:
        return "under"
    return "push"


def _ou_actual(actual, line):
    if actual > line:
        return "over"
    if actual < line:
        return "under"
    return "push"


def _ou_hit(pred, line, actual):
    chosen = _ou_pick(pred, line)
    truth = _ou_actual(actual, line)
    if chosen == "push" or truth == "push":
        return None
    return chosen == truth


DATE_RE = re.compile(r"picks_totals_(\d{4}-\d{2}-\d{2})\.csv$")


def _safe_float(s):
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def collect_ledger(picks_glob):
    ledger = []
    results_cache = {}

    paths = sorted(glob.glob(picks_glob))
    log.info("scanning %d picks_totals CSVs from %s", len(paths), picks_glob)

    for path in paths:
        m = DATE_RE.search(os.path.basename(path))
        if not m:
            log.debug("skipping non-dated file: %s", path)
            continue
        date_str = m.group(1)

        # Filter NUL bytes that Windows/FUSE-mount writes occasionally
        # leave trailing on these CSVs (otherwise csv.reader raises
        # "line contains NUL" on the first padded line).
        try:
            raw = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            log.warning("could not read %s: %s", path, e)
            continue
        raw = raw.replace("\x00", "")
        from io import StringIO
        reader = csv.DictReader(StringIO(raw))
        rows = list(reader)
        if not rows:
            continue

        if date_str not in results_cache:
            results_cache[date_str] = fetch_results(date_str)
        date_results = results_cache[date_str]
        if not date_results:
            log.warning("no statsapi results for %s -- skipping %d rows",
                        date_str, len(rows))
            continue

        kept = 0
        for r in rows:
            away = r.get("away_team", "")
            home = r.get("home_team", "")
            res = lookup_result(date_results, away, home)
            if not res or not res.get("is_final") or res.get("total") is None:
                continue
            line = _safe_float(r.get("total_line"))
            pred_raw = _safe_float(r.get("pred_runs"))
            pred_bvp = _safe_float(r.get("pred_runs_bvp_adjusted"))
            actual = float(res["total"])
            if line is None or pred_raw is None:
                continue

            ledger.append({
                "game_date":  date_str,
                "away":       away,
                "home":       home,
                "matchup":    f"{away}@{home}",
                "total_line": line,
                "pred_raw":   pred_raw,
                "pred_bvp":   pred_bvp,
                "actual":     actual,
                "err_raw":    pred_raw - actual,
                "err_bvp":    (pred_bvp - actual) if pred_bvp is not None else None,
                "raw_correct": _ou_hit(pred_raw, line, actual),
                "bvp_correct": (_ou_hit(pred_bvp, line, actual)
                                if pred_bvp is not None else None),
                "status":     res.get("status", ""),
            })
            kept += 1
        log.info("  %s: %d/%d rows graded", date_str, kept, len(rows))

    return ledger


def render_report(ledger, generated):
    raw_errs = [row["err_raw"] for row in ledger]
    bvp_errs = [row["err_bvp"] for row in ledger if row["err_bvp"] is not None]

    raw_hits  = [row["raw_correct"] for row in ledger if row["raw_correct"] is not None]
    bvp_hits  = [row["bvp_correct"] for row in ledger if row["bvp_correct"] is not None]

    rmse_raw = _rmse(raw_errs)
    rmse_bvp = _rmse(bvp_errs)
    mae_raw  = _mae(raw_errs)
    mae_bvp  = _mae(bvp_errs)

    hit_raw = (sum(1 for h in raw_hits if h) / len(raw_hits)) if raw_hits else None
    hit_bvp = (sum(1 for h in bvp_hits if h) / len(bvp_hits)) if bvp_hits else None

    n_raw = len(raw_errs)
    n_bvp = len(bvp_errs)

    def _fmt(v, unit=""):
        if v is None:
            return "n/a"
        if unit == "%":
            return f"{v*100:.1f}%"
        return f"{v:.2f}"

    def _delta(a, b, unit=""):
        if a is None or b is None:
            return "n/a"
        d = b - a
        if unit == "%":
            return f"{d*100:+.1f}pp"
        return f"{d:+.2f}"

    def _improvement(raw, adj, lower_is_better=True):
        if raw is None or adj is None or raw == 0:
            return None
        return ((raw - adj) / raw) if lower_is_better else ((adj - raw) / raw)

    def _improvement_fmt(raw, adj, lower_is_better=True):
        imp = _improvement(raw, adj, lower_is_better)
        if imp is None:
            return "n/a"
        return f"{imp*100:+.1f}%"

    rmse_imp = _improvement(rmse_raw, rmse_bvp, lower_is_better=True)

    lines = []
    lines.append(f"# BvP Totals Backtest -- {generated}")
    lines.append("")
    lines.append("## Pairwise comparison")
    lines.append("")
    lines.append("| Metric | Raw (pred_runs) | BvP-adjusted | Delta | Improvement % |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| RMSE | {_fmt(rmse_raw)} | {_fmt(rmse_bvp)} | {_delta(rmse_raw, rmse_bvp)} | "
        f"{_improvement_fmt(rmse_raw, rmse_bvp, lower_is_better=True)} |")
    lines.append(
        f"| MAE  | {_fmt(mae_raw)} | {_fmt(mae_bvp)} | {_delta(mae_raw, mae_bvp)} | "
        f"{_improvement_fmt(mae_raw, mae_bvp, lower_is_better=True)} |")
    lines.append(
        f"| O/U hit rate | {_fmt(hit_raw, '%')} | {_fmt(hit_bvp, '%')} | "
        f"{_delta(hit_raw, hit_bvp, '%')} | "
        f"{_improvement_fmt(hit_raw, hit_bvp, lower_is_better=False)} |")
    lines.append(f"| n_games | {n_raw} | {n_bvp} | -- | -- |")
    lines.append("")

    lines.append("## Per-game ledger")
    lines.append("")
    lines.append("| date | matchup | line | pred_runs | pred_bvp_adj | actual | raw_correct | adj_correct |")
    lines.append("|---|---|---:|---:|---:|---:|:---:|:---:|")
    for row in ledger:
        pb = "--" if row["pred_bvp"] is None else f"{row['pred_bvp']:.2f}"
        rc = "--" if row["raw_correct"] is None else ("Y" if row["raw_correct"] else "N")
        ac = "--" if row["bvp_correct"] is None else ("Y" if row["bvp_correct"] else "N")
        lines.append(
            f"| {row['game_date']} | {row['matchup']} | {row['total_line']:.1f} | "
            f"{row['pred_raw']:.2f} | {pb} | {row['actual']:.1f} | {rc} | {ac} |"
        )
    if not ledger:
        lines.append("| -- | (no graded games yet) | -- | -- | -- | -- | -- | -- |")
    lines.append("")

    lines.append("## Promotion gate")
    lines.append(
        f"- Rule 10 threshold: BvP RMSE improvement >= "
        f"{int(PROMOTION_GATE_RMSE_IMPROVEMENT*100)}%")
    if rmse_imp is None:
        lines.append(f"- Current: n/a")
    else:
        lines.append(f"- Current: {rmse_imp*100:+.1f}%")

    if n_bvp < PROMOTION_GATE_MIN_N:
        deficit = PROMOTION_GATE_MIN_N - n_bvp
        days_left = max(1, int(math.ceil(deficit / 10)))
        if n_bvp == 0:
            verdict = (
                f"INSUFFICIENT DATA -- n_games_with_shadow_col = 0; expected "
                f"after 7+ days of post-Task-1 ops (~{days_left} more daily "
                f"slates needed to reach n>={PROMOTION_GATE_MIN_N})."
            )
        else:
            verdict = (
                f"INSUFFICIENT DATA -- only {n_bvp} graded BvP-adjusted rows; "
                f"need ~{deficit} more (~{days_left} more daily slates)."
            )
    elif rmse_imp is not None and rmse_imp >= PROMOTION_GATE_RMSE_IMPROVEMENT:
        verdict = (
            f"PROMOTE -- BvP RMSE improvement {rmse_imp*100:.1f}% meets the "
            f"{int(PROMOTION_GATE_RMSE_IMPROVEMENT*100)}% threshold over "
            f"n={n_bvp} graded games."
        )
    else:
        actual_imp = "n/a" if rmse_imp is None else f"{rmse_imp*100:+.1f}%"
        verdict = (
            f"KEEP SHADOW -- BvP RMSE improvement {actual_imp} below the "
            f"{int(PROMOTION_GATE_RMSE_IMPROVEMENT*100)}% threshold "
            f"(n={n_bvp})."
        )
    lines.append(f"- Verdict: {verdict}")
    lines.append("")

    return "\n".join(lines)


def _default_out_path():
    today = _date.today().isoformat()
    return f"experiments/bvp_backtest_{today}.md"


def _default_picks_glob():
    return "docs/data/picks_totals_*.csv"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=None,
                   help="Output markdown path (default: experiments/bvp_backtest_<today>.md)")
    p.add_argument("--picks-glob", default=None,
                   help="Glob for picks_totals_*.csv files "
                        "(default: docs/data/picks_totals_*.csv)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=(logging.DEBUG if args.verbose else logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    picks_glob = args.picks_glob or _default_picks_glob()
    out_path = args.out or _default_out_path()

    ledger = collect_ledger(picks_glob)
    generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    report = render_report(ledger, generated)

    print(report)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(report, encoding="utf-8")
    log.info("wrote %s (%d ledger rows)", out_path, len(ledger))


if __name__ == "__main__":
    main()
