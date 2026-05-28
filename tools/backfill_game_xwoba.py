"""Backfill per-game team xwOBA log from Baseball Savant statcast_search/csv.

Stdlib + requests only. No pandas, no pybaseball.

Locked schema: data/postgame/game_xwoba_log_schema.md
Locked formula: sum(estimated_woba_using_speedangle) / sum(woba_denom)
                filtered to woba_denom > 0 (terminal PA rows).

Per-game grouping: inning_topbot='Top' => away batting,
                   inning_topbot='Bot' => home batting.

Output: data/postgame/game_xwoba_log.csv (one row per game_pk).

Usage:
  python tools/backfill_game_xwoba.py                       # backfill 2026-04-27 .. yesterday
  python tools/backfill_game_xwoba.py --start 2026-05-26 --end 2026-05-26  # single day
  python tools/backfill_game_xwoba.py --start 2026-05-26 --end 2026-05-28 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

SAVANT_CSV_URL = "https://baseballsavant.mlb.com/statcast_search/csv"
USER_AGENT = "Mozilla/5.0 mlb_edge-backfill"
RATE_LIMIT_SECONDS = 1.0  # be polite

# Savant -> diag CSV abbrev drift. Seed; auto-extend on first run if new
# drift is detected.
SAVANT_TO_DIAG_ABBREV = {
    "CWS": "CHW",   # White Sox: Savant says CWS, diag says CHW
    "ATH": "OAK",   # Athletics (recent Savant change): diag still uses OAK
}

OUT_CSV = "data/postgame/game_xwoba_log.csv"
OUT_HEADER = [
    "game_pk", "game_date", "home_team", "away_team",
    "home_xwoba", "away_xwoba",
    "home_score", "away_score",
    "n_pa_home", "n_pa_away",
    "source_pulled_at",
]


def fetch_date_csv(d: date, timeout: int = 60) -> str:
    """Pull one day of pitch-level Statcast CSV. Returns text or empty string."""
    params = {
        "all": "true",
        "hfGT": "R|",          # regular season only
        "hfSea": f"{d.year}|",
        "player_type": "batter",
        "game_date_gt": d.isoformat(),
        "game_date_lt": d.isoformat(),
        "type": "details",
    }
    r = requests.get(SAVANT_CSV_URL, params=params, timeout=timeout,
                     headers={"User-Agent": USER_AGENT})
    if r.status_code != 200:
        print(f"  WARN: {d} returned HTTP {r.status_code}", file=sys.stderr)
        return ""
    return r.text.lstrip("﻿")  # strip BOM


def aggregate_games(csv_text: str) -> dict:
    """Group pitch rows by game_pk and compute per-team xwOBA.

    Returns: dict[game_pk] -> dict with the OUT_HEADER columns (minus source_pulled_at).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    by_game = defaultdict(list)
    for row in reader:
        by_game[row["game_pk"]].append(row)

    out = {}
    for gpk, pitches in by_game.items():
        first = pitches[0]
        away_raw = first["away_team"].strip()
        home_raw = first["home_team"].strip()
        away = SAVANT_TO_DIAG_ABBREV.get(away_raw, away_raw)
        home = SAVANT_TO_DIAG_ABBREV.get(home_raw, home_raw)

        # Filter to terminal PA rows. woba_denom is either '' or '1' on Savant.
        terminal = [p for p in pitches if p.get("woba_denom") == "1"]

        # Split by inning_topbot: Top = away batting, Bot = home batting.
        away_pas = [p for p in terminal if p.get("inning_topbot") == "Top"]
        home_pas = [p for p in terminal if p.get("inning_topbot") == "Bot"]

        def _xwoba(pas):
            if not pas:
                return None, 0
            num = 0.0
            denom = 0
            for p in pas:
                xv = p.get("estimated_woba_using_speedangle", "")
                if xv == "" or xv == "null":
                    # Statcast leaves this null for some non-BIP terminal events
                    # that don't get a wOBA weight (e.g., catcher interference).
                    # Skip — don't pollute the average.
                    continue
                try:
                    num += float(xv)
                    denom += 1
                except ValueError:
                    continue
            if denom == 0:
                return None, 0
            return round(num / denom, 4), denom

        home_xwoba, n_pa_home = _xwoba(home_pas)
        away_xwoba, n_pa_away = _xwoba(away_pas)

        # Final score is the max home_score / away_score seen across pitches.
        # Savant stores running score per pitch; the last pitch has the final.
        def _score(side):
            vals = [int(p[side]) for p in pitches if p.get(side, "").isdigit()]
            return max(vals) if vals else None

        out[gpk] = {
            "game_pk": gpk,
            "game_date": first["game_date"],
            "home_team": home,
            "away_team": away,
            "home_xwoba": home_xwoba,
            "away_xwoba": away_xwoba,
            "home_score": _score("home_score"),
            "away_score": _score("away_score"),
            "n_pa_home": n_pa_home,
            "n_pa_away": n_pa_away,
        }
    return out


def write_csv(rows: list, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-04-27",
                    help="Start date inclusive (YYYY-MM-DD). Default: archive earliest.")
    ap.add_argument("--end", default=None,
                    help="End date inclusive. Default: yesterday.")
    ap.add_argument("--out", default=OUT_CSV)
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't write CSV; just print per-day summary.")
    args = ap.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end \
        else (datetime.now(timezone.utc).date() - timedelta(days=1))

    print(f"Backfill window: {start} -> {end} ({(end-start).days + 1} days)")
    print(f"Output: {args.out}{' (dry-run)' if args.dry_run else ''}")
    print()

    pulled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    all_rows = []
    seen_abbrevs = set()

    for d in daterange(start, end):
        text = fetch_date_csv(d)
        if not text:
            print(f"  {d}: no data")
            time.sleep(RATE_LIMIT_SECONDS)
            continue
        games = aggregate_games(text)
        for gpk, row in games.items():
            row["source_pulled_at"] = pulled_at
            all_rows.append(row)
            seen_abbrevs.add(row["home_team"])
            seen_abbrevs.add(row["away_team"])
        print(f"  {d}: {len(games)} games aggregated")
        time.sleep(RATE_LIMIT_SECONDS)

    print()
    print(f"Total games: {len(all_rows)}")
    print(f"Unique team abbrevs seen: {sorted(seen_abbrevs)}")

    if args.dry_run:
        print("\n--- dry-run sample (first 3 games) ---")
        for r in all_rows[:3]:
            print(r)
        return

    write_csv(all_rows, args.out)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
