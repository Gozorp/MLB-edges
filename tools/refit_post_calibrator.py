"""
tools/refit_post_calibrator.py
------------------------------
Re-fit the post-bake probability calibrator (models/calibration_v1.json) using
every picks_*_diag.csv currently baked under docs/data/, paired with the actual
game outcomes from MLB statsapi.

Designed to be run weekly by .github/workflows/refit-calibrator.yml.  Runs
without any heavy dependencies — pure stdlib + urllib.

Algorithm (same as the bootstrap fit used 2026-05-08):
    1. For each date in docs/data/manifest.json, fetch picks CSV + final
       results from statsapi.mlb.com.
    2. Pair each pick's stated model probability with whether the picked
       side actually won.
    3. Bin into 10 equal-width buckets, compute Beta(8)-shrunk hit rate
       per bucket (toward the bin midpoint as a prior).
    4. Force monotonicity via weighted PAV.
    5. Compute Brier-before and Brier-after.  Always write the new file
       (Beta-shrunk fit on more data is weakly better than less data).

Output: writes models/calibration_v1.json with the new table.

Usage:
    python tools/refit_post_calibrator.py            # default
    python tools/refit_post_calibrator.py --dry-run  # report only, do not write
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import logging
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

ABBR_FIX = {"CHW": "CWS", "WSH": "WSH", "OAK": "ATH",
            "KCR": "KC", "TBR": "TB", "ARI": "AZ"}


def fetch_results(date: str, retries: int = 3, sleep: float = 0.3) -> Dict[str, dict]:
    """Pull the schedule for one date and return a dict keyed by 'AWAY@HOME'."""
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=team"
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
        log.warning("results fetch failed for %s: %s", date, last_err)
        return {}
    out: Dict[str, dict] = {}
    for d in j.get("dates", []):
        for g in d.get("games", []):
            t = g["teams"]
            a = t["away"]["team"].get("abbreviation") or ""
            h = t["home"]["team"].get("abbreviation") or ""
            out[f"{a}@{h}"] = dict(
                away=a, home=h,
                away_score=t["away"].get("score"),
                home_score=t["home"].get("score"),
                status=g.get("status", {}).get("detailedState", ""),
            )
    return out


def lookup_result(date_results: dict, away: str, home: str) -> dict | None:
    keys = [f"{away}@{home}",
            f"{ABBR_FIX.get(away, away)}@{ABBR_FIX.get(home, home)}"]
    for k in keys:
        if k in date_results:
            return date_results[k]
    return None


def collect_pairs(repo_root: Path) -> List[Tuple[float, int]]:
    """Walk docs/data/picks_*_diag.csv, fetch outcomes, return (prob, won) pairs."""
    data_dir = repo_root / "docs" / "data"
    csvs = sorted(data_dir.glob("picks_*_diag.csv"))
    log.info("found %d picks CSVs in %s", len(csvs), data_dir)

    pairs: List[Tuple[float, int]] = []
    for path in csvs:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if not m:
            continue
        date = m.group(1)
        results = fetch_results(date)
        time.sleep(0.25)
        if not results:
            log.warning("no results for %s — skipping", date)
            continue

        with path.open() as f:
            for row in csv.DictReader(f):
                m2 = re.match(r"\s*([A-Z]+)\s*@\s*([A-Z]+)", row.get("matchup", ""))
                if not m2:
                    continue
                away, home = m2.group(1), m2.group(2)
                res = lookup_result(results, away, home)
                if not res or res["status"] not in ("Final", "Game Over", "Completed Early"):
                    continue
                if res["away_score"] is None or res["home_score"] is None:
                    continue
                pick = (row.get("pick") or "").strip()
                pick_norm = ABBR_FIX.get(pick, pick)
                pick_is_home = pick_norm == res["home"] or pick == res["home"]
                home_won = res["home_score"] > res["away_score"]
                pick_won = (pick_is_home and home_won) or (not pick_is_home and not home_won)
                try:
                    p = float(row.get("full_prob") or row.get("p_model") or 0)
                except (TypeError, ValueError):
                    continue
                if 0.0 < p < 1.0:
                    pairs.append((p, 1 if pick_won else 0))
    log.info("collected %d (prob, outcome) pairs", len(pairs))
    return pairs


def fit_calibrator(pairs: List[Tuple[float, int]],
                   n_bins: int = 10,
                   beta_prior: float = 8.0) -> List[dict]:
    buckets: List[List[int]] = [[] for _ in range(n_bins)]
    for p, y in pairs:
        idx = min(n_bins - 1, int(p * n_bins))
        buckets[idx].append(y)

    table = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        mid = (lo + hi) / 2
        n = len(buckets[i])
        k = sum(buckets[i])
        shrunk = (k + beta_prior * mid) / (n + beta_prior)
        table.append({"bin_lo": lo, "bin_hi": hi, "bin_mid": mid,
                      "n": n, "raw_hits": k,
                      "raw_rate": (k / n) if n else None,
                      "calibrated_rate": shrunk})

    # Weighted PAV for monotonicity
    rates = [r["calibrated_rate"] for r in table]
    weights = [max(r["n"], 1) for r in table]
    for i in range(1, n_bins):
        if rates[i] < rates[i - 1]:
            wt = weights[i - 1] + weights[i]
            avg = (rates[i - 1] * weights[i - 1] + rates[i] * weights[i]) / wt
            rates[i - 1] = rates[i] = avg
            weights[i - 1] = weights[i] = wt
            j = i - 1
            while j > 0 and rates[j - 1] > rates[j]:
                wt = weights[j - 1] + weights[j]
                avg = (rates[j - 1] * weights[j - 1] + rates[j] * weights[j]) / wt
                rates[j - 1] = rates[j] = avg
                weights[j - 1] = weights[j] = wt
                j -= 1
    for i, r in enumerate(table):
        r["calibrated_rate"] = rates[i]
    return table


def brier_score(pairs: List[Tuple[float, int]],
                table: List[dict] | None = None) -> float:
    if not pairs:
        return 0.0
    if table:
        mids = [r["bin_mid"] for r in table]
        rates = [r["calibrated_rate"] for r in table]

        def remap(p: float) -> float:
            if p <= mids[0]: return rates[0]
            if p >= mids[-1]: return rates[-1]
            for i in range(len(mids) - 1):
                if mids[i] <= p <= mids[i + 1]:
                    t = (p - mids[i]) / (mids[i + 1] - mids[i])
                    return rates[i] + t * (rates[i + 1] - rates[i])
            return p

        return sum((remap(p) - y) ** 2 for p, y in pairs) / len(pairs)
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only — do not write the JSON file.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    repo_root = args.repo_root
    out_path = repo_root / "models" / "calibration_v1.json"

    pairs = collect_pairs(repo_root)
    if len(pairs) < 30:
        log.warning("only %d pairs — too few to refit (need >=30); skipping", len(pairs))
        return 0

    new_table = fit_calibrator(pairs)
    new_brier = brier_score(pairs, new_table)
    raw_brier = brier_score(pairs)
    log.info("refit: n_samples=%d  Brier raw=%.4f  Brier new=%.4f  Δ=%+.4f (%.1f%%)",
             len(pairs), raw_brier, new_brier,
             new_brier - raw_brier, (raw_brier - new_brier) / raw_brier * 100)

    out = {
        "version": "v1",
        "fit_date": time.strftime("%Y-%m-%d", time.gmtime()),
        "n_samples": len(pairs),
        "n_bins": len(new_table),
        "beta_prior": 8.0,
        "brier_before": round(raw_brier, 4),
        "brier_after": round(new_brier, 4),
        "table": new_table,
    }

    if args.dry_run:
        print(json.dumps(out, indent=2))
        log.info("dry run — NOT writing %s", out_path)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    log.info("wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
