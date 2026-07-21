#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_lineups.py -- surgically refresh ONLY the baked lineup columns in a
slate's diag CSV, so the Statcast Game Preview shows today's real lineups.

WHY THIS EXISTS
---------------
The preview renders from two baked columns in docs/data/picks_<date>_diag.csv:
    away_top_5_batters_json   home_top_5_batters_json
Those are populated once, during the main_predict bake. MLB posts lineups only
~3h before first pitch, so the early bakes (the 06:00 UTC / ~1am night-owl run,
and even the 14:00 UTC / 10am ET run) capture an EMPTY lineup ("[]") for every
game -- and the preview then shows "Awaiting Starting Lineup" all day. The heavy
daily-slate re-runs later in the day don't reliably backfill them (started-game
lock, probables resolution, odds churn), so a small single-purpose job is the
robust fix.

WHAT IT DOES (model-safe)
-------------------------
Re-fetches each game's posted batting order via the SAME code the nightly bake
uses (platoon_splits.build_team_top_5_payload) and rewrites ONLY the two JSON
columns. Every other column is passed through the csv module as an untouched
string -- the model output (fair_prob, edge_pp, tier, ...) is never reparsed or
reformatted. A game whose lineup still isn't posted keeps whatever it already
had (an empty fetch NEVER overwrites existing data). Idempotent: re-runnable any
number of times; only newly-posted lineups change bytes.

Schedule it across the afternoon/evening (see .github/workflows/refresh-lineups.yml)
so the preview fills in as lineups drop. Docs are served from git, so a commit +
push must follow (the workflow does this; locally the hourly reset wipes anything
un-pushed).

Usage:  python tools/refresh_lineups.py [YYYY-MM-DD]     (default: today, UTC)
Exit 0 always on the happy path (incl. "nothing posted yet"); sandboxed per-row
so one game's fetch failure can't abort the slate.
"""
import csv
import datetime
import json
import os
import sys

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mlb_edge import platoon_splits as ps   # noqa: E402

AWAY_COL = "away_top_5_batters_json"
HOME_COL = "home_top_5_batters_json"


def _sp_hands(date):
    """SP throwing hand keyed by pitcher name, from the platoon sidecar if it
    exists (tools/platoon_enrichment.py writes it). Used only to resolve the
    per-batter vs_today_SP_* fields; a miss just leaves those null."""
    p = os.path.join("docs", "data", "platoon_%s.json" % date)
    try:
        with open(p, encoding="utf-8") as fh:
            j = json.load(fh)
        return {name: (rec or {}).get("hand")
                for name, rec in (j.get("pitchers") or {}).items()}
    except Exception:
        return {}


def _detect_newline(path):
    """Preserve the file's existing line ending so unchanged rows stay byte-
    identical (avoid a whole-file CRLF<->LF diff)."""
    with open(path, "rb") as fh:
        head = fh.read(65536)
    return "\r\n" if b"\r\n" in head else "\n"


def main():
    date = (sys.argv[1] if len(sys.argv) > 1
            else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"))
    path = os.path.join("docs", "data", "picks_%s_diag.csv" % date)
    if not os.path.exists(path):
        print("[refresh_lineups] no diag for %s (%s); skip" % (date, path))
        return 0

    newline = _detect_newline(path)
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if not fieldnames or AWAY_COL not in fieldnames or HOME_COL not in fieldnames:
        print("[refresh_lineups] %s missing lineup columns; skip" % path)
        return 0

    hands = _sp_hands(date)
    updated = 0
    for r in rows:
        matchup = (r.get("matchup") or "").strip()
        raw_pk = (r.get("game_pk") or "").strip()
        if not raw_pk:
            continue
        try:
            game_pk = int(float(raw_pk))
        except (TypeError, ValueError):
            continue
        away_hand = hands.get((r.get("away_sp_name") or "").strip())
        home_hand = hands.get((r.get("home_sp_name") or "").strip())
        try:
            # Away hitters face the HOME starter, and vice-versa.
            away = ps.build_team_top_5_payload(game_pk, "away", home_hand)
            home = ps.build_team_top_5_payload(game_pk, "home", away_hand)
        except Exception as e:
            print("[refresh_lineups] %-26s payload build failed: %s" % (matchup, e))
            continue
        # Only overwrite when a real lineup came back -- never wipe good data
        # back to "[]" if a fetch hiccups or the lineup is briefly pulled.
        changed = False
        if away:
            new = json.dumps(away, separators=(",", ":"))
            if new != (r.get(AWAY_COL) or ""):
                r[AWAY_COL] = new
                changed = True
        if home:
            new = json.dumps(home, separators=(",", ":"))
            if new != (r.get(HOME_COL) or ""):
                r[HOME_COL] = new
                changed = True
        if changed:
            updated += 1
            print("[refresh_lineups] %-26s away=%d home=%d" % (matchup, len(away), len(home)))

    if not updated:
        print("[refresh_lineups] no newly-posted lineups for %s; nothing written" % date)
        return 0

    # Atomic, string-preserving rewrite: only the two JSON columns differ.
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator=newline)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    print("[refresh_lineups] refreshed %d/%d games -> %s" % (updated, len(rows), path))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[refresh_lineups] WARN unexpected failure %r -- nothing written" % (e,))
        sys.exit(0)
