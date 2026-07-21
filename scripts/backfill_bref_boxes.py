"""Backfill B-R box scores using statsapi as the source of truth.

Why this exists alongside drain_bref_queue.py
---------------------------------------------
drain_bref_queue.py trusts data/.bref_scrape_pending.json. That queue is
lossy — entries written by game_end_watcher.py go missing (observed
2026-07-20: the queue held 1 entry, already scraped, while 485 finals since
2026-06-10 had no box file). A queue that silently drops work looks identical
to a queue that is legitimately empty, so the gap went unnoticed for ~5 weeks.

This script never reads the queue. It asks statsapi which games actually went
final in a date window, checks which box files are missing on disk, and
fetches only those. Re-running it is always safe and idempotent.

Usage
-----
    python scripts/backfill_bref_boxes.py                # audit + fetch last 7 days
    python scripts/backfill_bref_boxes.py --days 45      # wider window
    python scripts/backfill_bref_boxes.py --audit-only   # report gaps, fetch nothing

Notes
-----
* Same-day games are skipped: B-R often does not publish a box score until
  after midnight ET, so a same-day 404 is expected, not a failure.
* Doubleheaders use suffix 1/2 (from statsapi gameNumber), not 0. The
  url_for() helper in drain_bref_queue.py hardcodes 0 and is wrong on DH dates.
* 404 is treated as "not published yet" and never aborts the run; only hard
  errors (network/parse/rate-limit) trip the circuit breaker.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from drain_bref_queue import HEADERS, TEAM_TO_BREF, extract_box  # noqa: E402

OUT_DIR = ROOT / "data" / "bref" / "boxes"
LOG_DIR = ROOT / "logs"

DELAY_S = 3.5           # ~17 req/min, under B-R's ~20/min ceiling
MAX_CONSEC_FAIL = 6
MIN_TABLES = 4

log = logging.getLogger("backfill_bref")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"bref_backfill_{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def get_json(url: str, timeout: int = 60) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def team_id_to_bref() -> dict[int, str]:
    """Map statsapi team id -> B-R code, via the repo's own abbr mapping."""
    data = get_json("https://statsapi.mlb.com/api/v1/teams?sportId=1")
    out = {}
    for t in data.get("teams", []):
        code = TEAM_TO_BREF.get((t.get("abbreviation") or "").upper())
        if code:
            out[t["id"]] = code
        else:
            log.warning("no B-R code for team %s (%s)", t.get("name"), t.get("abbreviation"))
    return out


def find_missing(days: int) -> tuple[list[dict], list[dict]]:
    """Return (fetchable, deferred_today) missing box scores."""
    id2bref = team_id_to_bref()
    end = date.today()
    start = end - timedelta(days=days)
    sched = get_json(
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
        f"&startDate={start:%Y-%m-%d}&endDate={end:%Y-%m-%d}"
    )

    have = {p.name for p in OUT_DIR.glob("*.json")}
    missing = []
    for d in sched.get("dates", []):
        ds = d["date"]
        for g in d.get("games", []):
            if g.get("status", {}).get("codedGameState") != "F":
                continue
            # Regular season only — spring training ('S'), exhibition and
            # All-Star games live at different B-R URLs (or nowhere) and
            # would otherwise show up as permanent phantom gaps.
            if g.get("gameType") != "R":
                continue
            code = id2bref.get(g["teams"]["home"]["team"]["id"])
            if not code:
                continue
            # DH games are ...1 / ...2; single games are ...0
            dh = g.get("doubleHeader", "N")
            suffix = str(g.get("gameNumber", 1)) if dh in ("Y", "S") else "0"
            stamp = f"{code}{ds.replace('-', '')}{suffix}"
            fn = f"bref_boxscore_{stamp}.json"
            if fn in have:
                continue
            missing.append({
                "game_pk": g["gamePk"],
                "date": ds,
                "away": g["teams"]["away"]["team"].get("name", ""),
                "home": g["teams"]["home"]["team"].get("name", ""),
                "file": fn,
                "url": f"https://www.baseball-reference.com/boxes/{code}/{stamp}.shtml",
            })

    today = date.today().isoformat()
    deferred = [m for m in missing if m["date"] >= today]
    fetchable = sorted((m for m in missing if m["date"] < today),
                       key=lambda m: (m["date"], m["file"]), reverse=True)
    return fetchable, deferred


def fetch(url: str, retries: int = 1) -> str:
    """Fetch HTML. Never retries a 404 — that means 'not published', not 'flaky'."""
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:
            last = e
        if i < retries:
            time.sleep(5.0 * (i + 1))
    raise RuntimeError(f"fetch failed for {url}: {last!r}")


def is_rate_limited(html: str) -> bool:
    low = html[:4000].lower()
    return "too many requests" in low or "rate limited" in low


def scrape_one(item: dict) -> None:
    """Fetch, validate and atomically write one box score."""
    html = fetch(item["url"])
    if is_rate_limited(html):
        log.warning("rate-limited on %s — backing off 120s", item["file"])
        time.sleep(120)
        html = fetch(item["url"], retries=0)
        if is_rate_limited(html):
            raise RuntimeError("rate-limited after backoff")

    box = extract_box(html, item["url"])
    n = len(box["tables"])
    if n < MIN_TABLES:
        raise RuntimeError(f"only {n} tables (expected >={MIN_TABLES})")

    out_path = OUT_DIR / item["file"]
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(box, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)  # atomic — a reader never sees a half-written file


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    ap.add_argument("--audit-only", action="store_true", help="report gaps, fetch nothing")
    args = ap.parse_args()

    setup_logging()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fetchable, deferred = find_missing(args.days)
    log.info("window=%dd missing=%d fetchable=%d deferred_today=%d",
             args.days, len(fetchable) + len(deferred), len(fetchable), len(deferred))

    by_date: dict[str, int] = {}
    for m in fetchable:
        by_date[m["date"]] = by_date.get(m["date"], 0) + 1
    for ds in sorted(by_date):
        log.info("  gap %s: %d game(s)", ds, by_date[ds])

    if args.audit_only:
        return 0

    ok = notfound = hard_fail = 0
    consec = 0
    for i, item in enumerate(fetchable, 1):
        try:
            scrape_one(item)
            ok += 1
            consec = 0
            if ok % 25 == 0:
                log.info("[%d/%d] ok=%d 404=%d fail=%d", i, len(fetchable), ok, notfound, hard_fail)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                notfound += 1
                consec = 0  # not published yet — never trips the breaker
                log.info("[%d/%d] 404 (unpublished?) %s %s@%s",
                         i, len(fetchable), item["date"], item["away"], item["home"])
            else:
                hard_fail += 1
                consec += 1
                log.error("[%d/%d] HTTP %d %s", i, len(fetchable), e.code, item["file"])
        except Exception as e:
            hard_fail += 1
            consec += 1
            log.error("[%d/%d] FAIL %s: %r", i, len(fetchable), item["file"], e)

        if consec >= MAX_CONSEC_FAIL:
            log.error("%d consecutive hard failures — aborting, rest retried next run", consec)
            break
        time.sleep(DELAY_S)

    log.info("=== summary === ok=%d unpublished=%d failed=%d deferred_today=%d",
             ok, notfound, hard_fail, len(deferred))
    return 0


if __name__ == "__main__":
    sys.exit(main())
