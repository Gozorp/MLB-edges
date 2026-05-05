"""
live_check.py
-------------
On-demand snapshot of how today's slate is performing live. Pulls current
scores via MLB Stats API and cross-references against the bet sheet + audit
to show:

  - Status of each BET (winning/losing/pending)
  - Status of each SKIPped game where the model's lean was strong (so you
    can see in real time whether the conviction filter was over-cautious)

Usage:
    python scripts/live_check.py             # today
    python scripts/live_check.py --date 2026-04-26
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)


def fetch_live(day: date) -> list[dict]:
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {"sportId": 1, "date": day.isoformat(),
              "hydrate": "linescore,team"}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"MLB API fetch failed: {e}")
        return []
    out = []
    for dd in r.json().get("dates", []):
        for g in dd.get("games", []):
            ateam = g["teams"]["away"]["team"]
            hteam = g["teams"]["home"]["team"]
            ls = g.get("linescore", {})
            out.append({
                "away":   ateam.get("abbreviation") or ateam.get("teamCode", "?").upper(),
                "home":   hteam.get("abbreviation") or hteam.get("teamCode", "?").upper(),
                "as":     g["teams"]["away"].get("score"),
                "hs":     g["teams"]["home"].get("score"),
                "status": g["status"]["detailedState"],
                "inning": ls.get("currentInningOrdinal", ""),
                "state":  ls.get("inningState", ""),
            })
    return out


def status_emoji(g, our_pick: str) -> str:
    """Emoji for our pick relative to live state."""
    if g["status"] in ("Scheduled", "Pre-Game", "Warmup", "Delayed Start"):
        return "⏳"
    if g["status"] in ("Final", "Game Over", "Completed Early"):
        winner = g["home"] if (g["hs"] or 0) > (g["as"] or 0) else g["away"]
        return "✅" if winner == our_pick else "❌"
    # In progress
    if g["as"] is None or g["hs"] is None:
        return "▶️"
    leader = g["home"] if (g["hs"] or 0) > (g["as"] or 0) else g["away"]
    if g["hs"] == g["as"]:
        return "🟰"
    return "🔵" if leader == our_pick else "🔴"


def fmt_score(g) -> str:
    if g["status"] in ("Scheduled", "Pre-Game", "Warmup", "Delayed Start"):
        return f"({g['status']})"
    a = g["as"] if g["as"] is not None else "-"
    h = g["hs"] if g["hs"] is not None else "-"
    inn = f" {g['state']} {g['inning']}" if g["inning"] else ""
    return f"{g['away']} {a} - {g['home']} {h}  [{g['status']}{inn}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=lambda s: date.fromisoformat(s),
                    default=date.today())
    args = ap.parse_args()
    day = args.date

    audit_path = ROOT / f"audit_{day:%Y-%m-%d}.csv"
    picks_path = ROOT / f"picks_{day:%Y-%m-%d}.csv"
    if not audit_path.exists():
        print(f"ERROR: audit file not found at {audit_path}")
        return 1

    audit = pd.read_csv(audit_path)
    picks = pd.read_csv(picks_path) if picks_path.exists() else pd.DataFrame()
    picks_lookup = {}
    for _, p in picks.iterrows():
        for _, a in audit.iterrows():
            if a["away"] == p["team"] or a["home"] == p["team"]:
                picks_lookup[(a["away"], a["home"])] = p
                break

    live = {(g["away"], g["home"]): g for g in fetch_live(day)}

    print()
    print("━" * 75)
    print(f"  LIVE SLATE CHECK — {day.strftime('%A, %B %d, %Y')}")
    print("━" * 75)

    # ── BETS ─────────────────────────────────────────────────────────────
    print("\n💰 ACTIVE BETS:")
    if picks.empty:
        print("  (no bets today)")
    bet_pl = 0.0
    for _, a in audit.iterrows():
        key = (a["away"], a["home"])
        if key not in picks_lookup:
            continue
        p = picks_lookup[key]
        g = live.get(key, {"status": "Scheduled", "as": None, "hs": None,
                           "away": a["away"], "home": a["home"],
                           "inning": "", "state": ""})
        em = status_emoji(g, p["team"])
        line = f"  {em}  {p['team']:>4} ({p['tier']:<8} ${p['stake_u']:.2f})  →  {fmt_score(g)}"
        print(line)
        # tally settled bets
        if g["status"] in ("Final", "Game Over", "Completed Early"):
            winner = g["home"] if (g["hs"] or 0) > (g["as"] or 0) else g["away"]
            if winner == p["team"]:
                bet_pl += float(p["stake_u"]) * (float(p["decimal"]) - 1.0)
            else:
                bet_pl -= float(p["stake_u"])

    print(f"\n  💵 P/L on settled bets so far: ${bet_pl:+.2f}")

    # ── SKIP LEAN TRACKING ────────────────────────────────────────────────
    skip_w = skip_l = 0
    print("\n❌ SKIPPED GAMES (model's lean vs reality):")
    for _, a in audit.iterrows():
        if a["tier"] != "SKIP":
            continue
        key = (a["away"], a["home"])
        g = live.get(key, {"status": "Scheduled", "as": None, "hs": None,
                           "away": a["away"], "home": a["home"],
                           "inning": "", "state": ""})
        em = status_emoji(g, a["pick"])
        # Annotate the result if final
        verdict = ""
        if g["status"] in ("Final", "Game Over", "Completed Early"):
            winner = g["home"] if (g["hs"] or 0) > (g["as"] or 0) else g["away"]
            if winner == a["pick"]:
                verdict = "  → model lean WAS RIGHT (we missed)"
                skip_w += 1
            else:
                verdict = "  → skip was correct"
                skip_l += 1
        print(f"  {em}  lean: {a['pick']:>4} ({float(a['pick_prob']):.0f}%)  →  "
              f"{fmt_score(g)}{verdict}")

    if skip_w + skip_l > 0:
        print(f"\n  📊 SKIP lean track record (settled): "
              f"{skip_w}/{skip_w + skip_l} = "
              f"{100*skip_w/(skip_w+skip_l):.0f}% — "
              f"{'⚠️ vetoes look too tight' if skip_w/(skip_w+skip_l) > 0.55 else '✓ vetoes calibrated'}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
