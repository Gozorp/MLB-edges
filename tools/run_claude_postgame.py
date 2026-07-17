"""
tools/run_claude_postgame.py
----------------------------
Nightly post-mortem: for each completed game on a given date, send the slate
row + final outcome to Claude (Opus by default) and write the per-pick
analysis to docs/data/postgame/<date>.json so the dashboard can read it.

Designed to be run by .github/workflows/claude-postgame.yml at 03:30 UTC daily.

Usage:
    python tools/run_claude_postgame.py                # yesterday (UTC)
    python tools/run_claude_postgame.py --date 2026-05-09
    python tools/run_claude_postgame.py --date 2026-05-09 --dry-run

Output schema (docs/data/postgame/2026-05-09.json):
    {
      "date": "2026-05-09",
      "model": "claude-opus-4-6",
      "fit_at": "<ISO timestamp>",
      "n_analyzed": 14,
      "total_input_tokens": 12345,
      "total_output_tokens": 4321,
      "by_matchup": {
        "HOU @ CIN": {
          "verdict": "LOSS",
          "headline": "...",
          "hypothesis": "...",
          "signals_to_recheck": ["bp_min", "fair_prob"]
        },
        ...
      }
    }
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the script runnable from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mlb_edge import claude_analyzer as ca

log = logging.getLogger(__name__)

ABBR_FIX = {"CHW": "CWS", "WSH": "WSH", "OAK": "ATH",
            "KCR": "KC", "TBR": "TB", "ARI": "AZ"}


def fetch_results(date: str) -> Dict[str, Dict[str, Any]]:
    """One-shot fetch of MLB schedule + scores for a given date."""
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=team"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            j = json.load(r)
    except Exception as e:
        log.warning("fetch_results(%s) failed: %s", date, e)
        return {}
    # Doubleheader-safe (2026-07-17): each key holds a LIST of games in
    # gameNumber order — previously G2 overwrote G1 under the same key, so
    # G1 could be graded against G2's score and G2 was never analyzed.
    out: Dict[str, list] = {}
    for d in j.get("dates", []):
        for g in sorted(d.get("games", []), key=lambda x: x.get("gameNumber") or 1):
            t = g["teams"]
            a = t["away"]["team"].get("abbreviation") or ""
            h = t["home"]["team"].get("abbreviation") or ""
            ph = (t["home"].get("probablePitcher") or {}).get("fullName")
            pa = (t["away"].get("probablePitcher") or {}).get("fullName")
            out.setdefault(f"{a}@{h}", []).append(dict(
                away=a, home=h,
                away_score=t["away"].get("score"),
                home_score=t["home"].get("score"),
                status=g.get("status", {}).get("detailedState", ""),
                statusText=g.get("status", {}).get("detailedState", ""),
                away_pitcher=pa, home_pitcher=ph,
                gamePk=g.get("gamePk"),
                gameNumber=g.get("gameNumber") or 1,
            ))
    return out


def lookup(results: dict, away: str, home: str,
           occurrence: int = 0) -> Optional[Dict[str, Any]]:
    """occurrence: 0 = first game of the day for this matchup, 1 = DH game 2."""
    keys = [f"{away}@{home}",
            f"{ABBR_FIX.get(away, away)}@{ABBR_FIX.get(home, home)}"]
    for k in keys:
        games = results.get(k)
        if games and occurrence < len(games):
            return games[occurrence]
    return None


def parse_claude_json(text: str) -> Dict[str, Any]:
    """Claude's response is supposed to be pure JSON, but defend against
    a stray markdown code-fence wrapper."""
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, count=1)
        s = re.sub(r"\s*```$", "", s, count=1)
    try:
        return json.loads(s)
    except Exception:
        return {"verdict": "UNKNOWN", "headline": "<parse error>",
                "hypothesis": text[:500], "signals_to_recheck": []}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="ISO date YYYY-MM-DD; defaults to yesterday UTC")
    p.add_argument("--dry-run", action="store_true",
                   help="Build prompts but do not call the API")
    p.add_argument("--repo-root", type=Path,
                   default=Path(__file__).resolve().parents[1])
    p.add_argument("--max-games", type=int, default=20,
                   help="Safety cap on games per run.")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.date:
        date = args.date
    else:
        # yesterday UTC
        yest = dt.datetime.utcnow().date() - dt.timedelta(days=1)
        date = yest.isoformat()

    repo = args.repo_root
    csv_path = repo / "docs" / "data" / f"picks_{date}_diag.csv"
    if not csv_path.exists():
        log.error("no slate CSV for %s at %s — bailing out", date, csv_path)
        return 1

    results = fetch_results(date)
    if not results:
        log.warning("no MLB results for %s yet — nothing to analyze", date)
        return 0

    rows = list(csv.DictReader(csv_path.open()))
    log.info("loaded %d picks for %s", len(rows), date)

    out_dir = repo / "docs" / "data" / "postgame"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{date}.json"

    # Resume support: if file already exists, load it so we only re-analyze
    # rows we haven't already done.
    existing: Dict[str, Any] = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except Exception:
            existing = {}
    by_matchup: Dict[str, Any] = (existing or {}).get("by_matchup", {})

    total_in = total_out = 0
    n_processed = 0
    occ_seen: Dict[str, int] = {}   # DH-safe: nth diag row for a matchup = nth game
    for row in rows:
        if n_processed >= args.max_games:
            break
        m = re.match(r"\s*([A-Z]+)\s*@\s*([A-Z]+)", row.get("matchup", ""))
        if not m:
            continue
        away, home = m.group(1), m.group(2)
        occ = occ_seen.get(row["matchup"], 0)
        occ_seen[row["matchup"]] = occ + 1
        # storage key: bare matchup for game 1 (back-compat with every
        # by_matchup reader), " (G2)" suffix for doubleheader game 2+
        mkey = row["matchup"] if occ == 0 else "%s (G%d)" % (row["matchup"], occ + 1)
        res = lookup(results, away, home, occ)
        if not res or res["status"] not in ("Final", "Game Over", "Completed Early"):
            continue
        if res["away_score"] is None or res["home_score"] is None:
            continue
        if mkey in by_matchup:
            log.info("  %s already analyzed — skipping", mkey)
            continue

        if args.dry_run:
            log.info("  [dry] would analyze %s", mkey)
            n_processed += 1
            continue

        log.info("  analyzing %s (pick %s vs %s-%s) ...",
                 mkey, row.get("pick"),
                 res["away_score"], res["home_score"])
        resp = ca.postgame_for_pick(row, res)
        if not resp.ok:
            log.warning("  -> Claude error: %s", resp.error)
            by_matchup[mkey] = {
                "verdict": "ERROR", "headline": resp.error[:200],
                "hypothesis": "", "signals_to_recheck": [],
            }
        else:
            parsed = parse_claude_json(resp.text)
            by_matchup[mkey] = parsed
            total_in += resp.input_tokens
            total_out += resp.output_tokens
            log.info("  -> %s: %s",
                     parsed.get("verdict", "?"), parsed.get("headline", "")[:80])
        n_processed += 1
        time.sleep(0.5)  # gentle pacing — Anthropic's tier-1 limits are forgiving

    final = {
        "date": date,
        "model": ca.DEFAULT_MODEL,
        "fit_at": dt.datetime.utcnow().isoformat() + "Z",
        "n_analyzed": len(by_matchup),
        "total_input_tokens": total_in + (existing.get("total_input_tokens") or 0),
        "total_output_tokens": total_out + (existing.get("total_output_tokens") or 0),
        "by_matchup": by_matchup,
    }
    if not args.dry_run:
        out_path.write_text(json.dumps(final, indent=2))
        log.info("wrote %s — %d entries, ~%d in / %d out tokens this run",
                 out_path, len(by_matchup), total_in, total_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
