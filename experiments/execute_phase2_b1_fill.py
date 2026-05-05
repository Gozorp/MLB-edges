"""Execute the Phase 2 b1 odds-cache fill plan.

Reads data/odds_cache/fill_plan_b1_prioritized.json, calls
OddsClient.historical_snapshot() for each timestamp, logs quota remaining
after each call. Aborts if x-requests-remaining drops below 200.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_edge import data_ingestion as di
from mlb_edge.config import DATA

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
log = logging.getLogger("phase2_b1_fill")


PLAN_PATH = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\fill_plan_b1_v2.json")
ABORT_THRESHOLD = 200


def main():
    plan = json.loads(PLAN_PATH.read_text())
    calls = plan["calls"]
    print(f"Plan: {len(calls)} calls, est. quota: {len(calls)*10} requests")
    ic = plan["initial_coverage"]
    print(f"Initial coverage: 2024 {ic['2024']['paired']}/{ic['2024']['total']}, "
          f"2025 {ic['2025']['paired']}/{ic['2025']['total']}")

    client = di.OddsClient()
    if not client.api_key:
        log.error("ODDS_API_KEY not set — aborting")
        return

    log_path = Path(r"D:\mlb_edge\mlb_edge\data\odds_cache\fill_b1_log.jsonl")
    fh = open(log_path, "w", encoding="utf-8")

    quota_remaining = None
    aborted = False

    for i, call in enumerate(calls, 1):
        ts = call["ts"]
        gd = call["gd"]
        impact = call["impact_total"]

        # Quota check via header from previous call
        if quota_remaining is not None and quota_remaining < ABORT_THRESHOLD:
            log.error(f"Quota remaining {quota_remaining} < {ABORT_THRESHOLD}. Aborting.")
            aborted = True
            break

        # Pre-call check: cache hit?
        from hashlib import md5
        cache_key = ts  # historical_snapshot uses iso ts as key
        cached_path = client._cache_path("historical", cache_key)
        if cached_path.exists():
            log.info(f"[{i}/{len(calls)}] gd={gd} ts={ts} impact={impact} → CACHE HIT, skipping")
            fh.write(json.dumps({
                "call_idx": i, "gd": gd, "ts": ts, "impact": impact,
                "result": "cache_hit", "quota_remaining": quota_remaining,
            }) + "\n")
            fh.flush()
            continue

        # Make the call
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # historical_snapshot expects naive UTC datetime
        ts_naive = ts_dt.replace(tzinfo=None)

        # Capture remaining via direct GET (so we can read header)
        url = f"{DATA.odds_api_base}/historical/sports/{DATA.odds_sport}/odds"
        params = {
            "apiKey": client.api_key,
            "regions": DATA.odds_regions,
            "markets": DATA.odds_markets,
            "oddsFormat": "american",
            "date": ts,
        }
        try:
            r = requests.get(url, params=params, timeout=20)
        except requests.RequestException as e:
            log.error(f"[{i}/{len(calls)}] gd={gd} ts={ts} request_exception={e}")
            fh.write(json.dumps({
                "call_idx": i, "gd": gd, "ts": ts, "impact": impact,
                "result": "exception", "error": str(e),
                "quota_remaining": quota_remaining,
            }) + "\n")
            fh.flush()
            time.sleep(1)
            continue

        remaining = r.headers.get("x-requests-remaining")
        used = r.headers.get("x-requests-used")
        if remaining is not None:
            quota_remaining = int(remaining)
        result = "ok"
        n_games = 0
        if r.status_code == 200:
            data = r.json()
            n_games = len(data.get("data", []))
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            cached_path.write_text(json.dumps(data), encoding="utf-8")
        else:
            result = f"http_{r.status_code}"
            log.error(f"[{i}/{len(calls)}] gd={gd} ts={ts} status={r.status_code} body={r.text[:200]}")

        log.info(
            f"[{i}/{len(calls)}] gd={gd} ts={ts} impact={impact} → {result} "
            f"games={n_games} remaining={remaining} used={used}"
        )
        fh.write(json.dumps({
            "call_idx": i, "gd": gd, "ts": ts, "impact": impact,
            "result": result, "n_games": n_games,
            "quota_remaining": quota_remaining, "quota_used": int(used) if used else None,
        }) + "\n")
        fh.flush()

        # Tiny pacing
        time.sleep(0.3)

    fh.close()
    print(f"\nDone. Aborted={aborted}. Final quota remaining: {quota_remaining}")
    print(f"Log: {log_path}")


if __name__ == "__main__":
    main()
