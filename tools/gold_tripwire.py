# -*- coding: utf-8 -*-
"""GOLD-tier performance tripwire (READ-ONLY, freeze-safe).

Pre-registered spec: GOLD_TRIPWIRE_PREREG.md (LOCKED 2026-06-24).
Watches the realized GOLD-tier win rate over a trailing 30-day, confirmed-final
window and pings the existing Discord health webhook if it breaches a
variance-aware floor on adequate sample. Touches no model state.

Usage:
  python tools/gold_tripwire.py            # evaluate + alert (rate-limited)
  python tools/gold_tripwire.py --dry-run  # evaluate + print, never post
"""
import os, sys, json, math, csv, glob, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, "docs", "data", "oos_ledger.jsonl")
SNAP = os.path.join(REPO, "docs", "data", "gold_tripwire.json")
STATE = os.path.join(REPO, "docs", "data", "gold_tripwire_state.json")
WEBHOOK = os.environ.get("DISCORD_HEALTH_WEBHOOK", "").strip()

# ---- LOCKED PARAMETERS (see GOLD_TRIPWIRE_PREREG.md) ----
P0 = 0.57            # historical GOLD baseline win rate
WINDOW_DAYS = 30     # trailing calendar-day window
MIN_N = 45           # sample gate
K_YELLOW = 1.5       # -1.5 sigma
K_RED = 2.0          # -2.0 sigma
RATE_LIMIT_H = 24.0  # same level no more than once per this many hours

DRY = "--dry-run" in sys.argv


def _load_tier_map():
    m = {}
    for p in glob.glob(os.path.join(REPO, "docs", "data", "picks_*_diag.csv")):
        d = os.path.basename(p).replace("picks_", "").replace("_diag.csv", "")
        try:
            for r in csv.DictReader(open(p, encoding="utf-8", errors="replace")):
                m[(d, (r.get("matchup") or "").strip())] = (r.get("tier") or "").strip()
        except Exception:
            pass
    return m


def evaluate():
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    results = [r for r in rows
               if r.get("phase") == "result" and not r.get("voided")
               and r.get("pick_correct") is not None and r.get("slate_date")]
    if not results:
        return {"status": "INSUFFICIENT", "n": 0, "reason": "no scored rows"}

    anchor = max(r["slate_date"] for r in results)
    anchor_d = datetime.strptime(anchor, "%Y-%m-%d").date()
    cutoff = anchor_d - timedelta(days=WINDOW_DAYS)
    tier_map = _load_tier_map()

    gold = []
    for r in results:
        try:
            d = datetime.strptime(r["slate_date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if d <= cutoff:
            continue
        tier = tier_map.get((r["slate_date"], (r.get("matchup") or "").strip()), (r.get("model_tier") or "")).strip()
        if tier == "GOLD":
            gold.append(int(bool(r["pick_correct"])))

    n = len(gold)
    snap = {"checked_at": datetime.now(timezone.utc).isoformat(), "window_days": WINDOW_DAYS,
            "anchor_slate": anchor, "p0": P0, "n_gold": n, "min_n": MIN_N}
    if n < MIN_N:
        snap.update({"status": "INSUFFICIENT", "win_pct": (sum(gold)/n if n else None),
                     "reason": f"n={n} < gate {MIN_N}"})
        return snap

    wins = sum(gold)
    win_pct = wins / n
    sigma = math.sqrt(P0 * (1 - P0) / n)
    yellow_floor = P0 - K_YELLOW * sigma
    red_floor = P0 - K_RED * sigma
    if win_pct < red_floor:
        status = "RED"
    elif win_pct < yellow_floor:
        status = "YELLOW"
    else:
        status = "GREEN"
    snap.update({"status": status, "win_pct": round(win_pct, 4), "wins": wins,
                 "sigma": round(sigma, 4), "yellow_floor": round(yellow_floor, 4),
                 "red_floor": round(red_floor, 4)})
    return snap


def _load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def _should_alert(status, state):
    if status not in ("YELLOW", "RED"):
        return False
    last = state.get(status)
    if not last:
        return True
    try:
        dt = datetime.fromisoformat(last)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0 >= RATE_LIMIT_H
    except Exception:
        return True


def _post_discord(snap):
    if not WEBHOOK:
        print("[tripwire] DISCORD_HEALTH_WEBHOOK unset; not posting", file=sys.stderr)
        return False
    color = 0xF85149 if snap["status"] == "RED" else 0xD29922
    icon = "\U0001F534" if snap["status"] == "RED" else "\U0001F7E1"
    title = f"{icon} mlb_edge: GOLD tier {snap['status']} — realized win {snap['win_pct']*100:.1f}%"
    desc = (f"Trailing {WINDOW_DAYS}d GOLD: **{snap['wins']}/{snap['n_gold']} = {snap['win_pct']*100:.1f}%** "
            f"(baseline {P0*100:.0f}%).\n"
            f"YELLOW floor {snap['yellow_floor']*100:.1f}% / RED floor {snap['red_floor']*100:.1f}% "
            f"(n={snap['n_gold']}, σ={snap['sigma']*100:.1f}pp).\n\n"
            + ("**RED:** ~97.7% this is beyond variance — staleness likely. Consider reducing unit "
               "sizing or a manual weights refresh. (Tripwire never changes the model.)"
               if snap["status"] == "RED" else
               "**YELLOW:** historically rare stretch. No action — monitor the next few slates."))
    payload = {"embeds": [{"title": title, "description": desc, "color": color,
                           "timestamp": datetime.now(timezone.utc).isoformat(),
                           "footer": {"text": "GOLD tripwire · rate-limited 24h · read-only"}}]}
    try:
        req = urllib.request.Request(WEBHOOK, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "mlb-edge-gold-tripwire/1"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"[tripwire] Discord post failed: {e}", file=sys.stderr)
        return False


def main():
    snap = evaluate()
    print("[tripwire]", json.dumps(snap))
    # always write snapshot (pull half)
    try:
        json.dump(snap, open(SNAP, "w", encoding="utf-8"), indent=1)
    except Exception as e:
        print(f"[tripwire] snapshot write failed: {e}", file=sys.stderr)
    state = _load_state()
    if _should_alert(snap.get("status"), state) and not DRY:
        if _post_discord(snap):
            state[snap["status"]] = datetime.now(timezone.utc).isoformat()
            try:
                json.dump(state, open(STATE, "w", encoding="utf-8"), indent=1)
            except Exception:
                pass
            print(f"[tripwire] alerted {snap['status']}")
    elif DRY:
        print(f"[tripwire] DRY-RUN: would{'' if _should_alert(snap.get('status'), state) else ' NOT'} alert ({snap.get('status')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
