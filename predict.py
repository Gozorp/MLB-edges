"""
predict.py - Single-command MLB-edge slate runner.

Usage:
    python predict.py
    python predict.py 2026-04-27
    python predict.py --bets-only
    python predict.py --help

Wraps the three-step pipeline (savant scrape -> weight update -> slate
prediction) behind one entry point. Loads .env at startup so the Odds API
key flows through without manual `set ODDS_API_KEY=...`.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from tools.slate_date import slate_today

log = logging.getLogger("predict")


def _load_dotenv() -> None:
    """Tiny zero-dependency .env loader. Pushes KEY=VALUE lines from
    ``./.env`` into os.environ unless already set in the real shell."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="predict",
        description="Run the full MLB-edge pipeline for a single slate date.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  predict                      # today, full diagnostic table\n"
            "  predict 2026-04-27           # specific date\n"
            "  predict --bets-only          # only games clearing every gate\n"
            "  predict --skip-all-prep      # skip scrape + weight update\n"
        ),
    )
    p.add_argument("slate_date", nargs="?", default=None,
                   help="Slate date YYYY-MM-DD (default: today).")
    p.add_argument("--bets-only", action="store_true",
                   help="Print only the recommended bet sheet.")
    p.add_argument("--bankroll", type=float, default=100.0,
                   help="Bankroll units for stake sizing (default: 100).")
    p.add_argument("--skip-scrape", action="store_true",
                   help="Skip the Savant leaderboard refresh.")
    p.add_argument("--skip-weights", action="store_true",
                   help="Skip the auto-weight-update for yesterday.")
    p.add_argument("--skip-all-prep", action="store_true",
                   help="--skip-scrape + --skip-weights.")
    p.add_argument("--no-news", action="store_true",
                   help="Skip the live-news enrichment layer.")
    p.add_argument("--out", default=None, help="Output CSV path.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable INFO-level logging.")
    return p.parse_args(argv)


def _resolve_date(value):
    # Defaults resolve in US Eastern (the MLB slate day) via tools/slate_date,
    # not the PC-local or UTC date -- see the 2026-07-12 evening-mismatch bug.
    if value is None or value.lower() == "today":
        return slate_today()
    if value.lower() == "yesterday":
        return slate_today() - timedelta(days=1)
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as e:
        raise SystemExit(f"[predict] bad date '{value}': use YYYY-MM-DD") from e


def main(argv=None):
    _load_dotenv()
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    slate_date = _resolve_date(args.slate_date)
    print(f"[predict] slate date: {slate_date.isoformat()}")

    skip_scrape = args.skip_scrape or args.skip_all_prep
    skip_weights = args.skip_weights or args.skip_all_prep

    if not skip_scrape:
        print("[predict] step 1/3: refreshing Savant leaderboards...")
        try:
            from mlb_edge import savant_scraper
            results = savant_scraper.refresh_all(
                slate_date.year, include_supplementary=True, overwrite=True,
            )
            n_ok = sum(1 for v in results.values() if v is not None)
            print(f"[predict] step 1/3 (Savant): {n_ok}/{len(results)} endpoints OK")
        except Exception as e:
            print(f"[predict] step 1/3 (Savant): FAILED ({e}) -- continuing with stale data")

        # FanGraphs SP scrape — daily-refresh source the parlay grader's
        # _load_sp_xstats() now prefers over Savant.  Failure here is
        # non-fatal: the grader will fall back to Savant automatically.
        print("[predict] step 1/3 (FanGraphs): refreshing SP leaderboards...")
        try:
            from mlb_edge import fangraphs_scraper
            fg_results = fangraphs_scraper.refresh_all(
                slate_date.year, overwrite=True,
            )
            fg_ok = sum(1 for v in fg_results.values() if v is not None)
            print(f"[predict] step 1/3 (FanGraphs): {fg_ok}/{len(fg_results)} endpoints OK")
        except Exception as e:
            print(f"[predict] step 1/3 (FanGraphs): FAILED ({e}) -- "
                  "grader will fall back to Savant cache")
    else:
        print("[predict] step 1/3: skipped (--skip-scrape)")

    if not skip_weights:
        print("[predict] step 2/3: auto-weight-update for yesterday...")
        try:
            from mlb_edge import auto_weight_update as awu
            awu.run(slate_date - timedelta(days=1))
            print("[predict] step 2/3: weights updated")
        except Exception as e:
            print(f"[predict] step 2/3: FAILED ({e}) -- continuing")
    else:
        print("[predict] step 2/3: skipped (--skip-weights)")

    print("[predict] step 3/3: scoring slate...")
    out_path = args.out
    if out_path is None:
        suffix = "" if args.bets_only else "_diag"
        out_path = f"picks_{slate_date.isoformat()}{suffix}.csv"

    from mlb_edge import main_predict as mp
    mp.run(
        slate_date,
        bankroll=args.bankroll,
        out_picks=out_path,
        diagnostic_table=not args.bets_only,
        skip_auto_update=True,
        skip_savant_refresh=True,
        skip_news=args.no_news,
    )

    print(f"[predict] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
