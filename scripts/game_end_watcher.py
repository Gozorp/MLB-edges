"""
game_end_watcher.py
-------------------
Long-running poll loop. Watches today's MLB schedule via the MLB Stats API.
When a game transitions to "Final" (or "Game Over" / "Completed Early"),
triggers the intraday recalibration pipeline to refresh data and re-predict
the remaining slate.

Also handles end-of-day self-improvement tracking: once every game on the
day is final (or postponed), writes outcomes_YYYYMMDD.csv comparing each
predicted pick to the actual result, and updates the rolling tier hit-rate
metrics in metrics/tier_performance.csv.

State persisted to data/.watcher_state_YYYYMMDD.json so restarts don't
re-trigger recalibration on already-processed games.

Architecture:
  - Polls every POLL_INTERVAL_S (default 300s = 5 min)
  - Triggers recalibrate_intraday.py via subprocess on game-end
  - Writes a Chrome-pending flag (data/.bref_scrape_pending.json) for the
    Claude scheduled task to pick up — that task does the B-R box score
    scrape via Chrome MCP, which can't be done from pure Python because
    B-R is Cloudflare-blocked.
  - At end of day, runs the outcomes tracker and updates metrics

Usage:
  python scripts/game_end_watcher.py             # foreground (debug)
  start /min python scripts/game_end_watcher.py  # background (Windows)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Set

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
METRICS = ROOT / "metrics"
METRICS.mkdir(exist_ok=True)

POLL_INTERVAL_S = 300         # 5 min
RECAL_TIMEOUT_S = 1200        # 20 min cap on a single recalibration
TERMINAL_STATUSES = {"Final", "Game Over", "Completed Early"}
POSTPONED_STATUSES = {"Postponed", "Cancelled", "Suspended"}

LOG_FILE = LOGS / f"game_end_watcher_{date.today():%Y%m%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("game_end_watcher")


def state_path(day: date) -> Path:
    return ROOT / "data" / f".watcher_state_{day:%Y%m%d}.json"


def load_state(day: date) -> Dict:
    p = state_path(day)
    if not p.exists():
        return {"date": day.isoformat(), "processed_game_pks": [],
                "end_of_day_done": False}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        log.warning("state file corrupt — starting fresh")
        return {"date": day.isoformat(), "processed_game_pks": [],
                "end_of_day_done": False}


def save_state(day: date, state: Dict) -> None:
    p = state_path(day)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch_schedule(day: date) -> List[Dict]:
    """Pull today's schedule with linescore + final scores via MLB Stats API."""
    url = "https://statsapi.mlb.com/api/v1/schedule"
    params = {
        "sportId": 1,
        "date": day.isoformat(),
        "hydrate": "linescore,team",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error("MLB Stats API fetch failed: %s", e)
        return []

    games = []
    for dd in data.get("dates", []):
        for g in dd.get("games", []):
            ateam = g["teams"]["away"]["team"]
            hteam = g["teams"]["home"]["team"]
            games.append({
                "game_pk": g.get("gamePk"),
                "status": g.get("status", {}).get("detailedState"),
                "home_team": hteam.get("abbreviation") or hteam.get("teamCode", "?").upper(),
                "away_team": ateam.get("abbreviation") or ateam.get("teamCode", "?").upper(),
                "home_score": g["teams"]["home"].get("score"),
                "away_score": g["teams"]["away"].get("score"),
            })
    return games


def trigger_recalibration(game_pk: int) -> bool:
    log.info("[recalibrate] triggering for game_pk=%d", game_pk)
    # Windows-only: hide child-process console window. `creationflags` is
    # not a valid kwarg on POSIX subprocess, so omit entirely off-Windows.
    win_kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        p = subprocess.run(
            [sys.executable, "scripts/recalibrate_intraday.py",
             "--game-pk", str(game_pk)],
            cwd=ROOT, timeout=RECAL_TIMEOUT_S, **win_kw,
        )
        ok = p.returncode == 0
        log.info("[recalibrate] %s for game_pk=%d (rc=%d)",
                 "OK" if ok else "FAIL", game_pk, p.returncode)
        return ok
    except subprocess.TimeoutExpired:
        log.error("[recalibrate] TIMEOUT after %ds for game_pk=%d",
                  RECAL_TIMEOUT_S, game_pk)
        return False
    except Exception as e:
        log.error("[recalibrate] EXCEPTION %s", e)
        return False


def queue_chrome_scrape(game_pk: int, away: str, home: str, day: date) -> None:
    """Append a flag for the Claude scheduled task to pick up (B-R box score
    scrape via Chrome MCP, which we can't do from pure Python because B-R
    is Cloudflare-blocked).
    """
    flag_path = ROOT / "data" / ".bref_scrape_pending.json"
    pending: List[Dict] = []
    if flag_path.exists():
        try:
            pending = json.loads(flag_path.read_text(encoding="utf-8"))
        except Exception:
            pending = []
    pending.append({
        "queued_at": datetime.now().isoformat(timespec="seconds"),
        "game_pk": game_pk,
        "away": away,
        "home": home,
        "date": day.isoformat(),
    })
    flag_path.write_text(json.dumps(pending, indent=2), encoding="utf-8")
    log.info("[chrome-queue] queued bref scrape for %s @ %s (game_pk=%d)",
             away, home, game_pk)


def _classify_veto(notes: str) -> str:
    """Bucket the SKIP reason from the audit's notes column."""
    n = (notes or "").lower()
    if "f1 negative" in n and "veto" in n and "suppressed" not in n:
        return "F1_negative_veto"
    if "f5 bullpen veto" in n:
        return "F5_bullpen_veto"
    if "small sample" in n or "suppressed" in n:
        return "small_sample_suppression"
    return "other_skip"


def write_outcomes(day: date, games: List[Dict]) -> None:
    """End-of-day outcome tracking. Writes:
        outcomes_YYYY-MM-DD.csv          — per-bet W/L
        outcomes_skipped_YYYY-MM-DD.csv  — model "lean" picks for SKIPped
                                           games (would-have-been-right rate)
    Updates two metrics tables that build the self-improvement substrate:
        metrics/tier_performance.csv  — bet hit rate / ROI by tier
        metrics/veto_performance.csv  — were our skips correct? bucketed
                                        by veto type. If F1_negative_veto
                                        skips win 58%+ over a 14d window,
                                        the gate threshold is too tight.
    """
    import pandas as pd

    picks_path = ROOT / f"picks_{day:%Y-%m-%d}.csv"
    audit_path = ROOT / f"audit_{day:%Y-%m-%d}.csv"
    outcomes_path = ROOT / f"outcomes_{day:%Y-%m-%d}.csv"
    skipped_outcomes_path = ROOT / f"outcomes_skipped_{day:%Y-%m-%d}.csv"

    # Build winner-by-team-pair map (we don't have game_pk in the audit)
    winners_pair: Dict[tuple, str] = {}
    winners_pk: Dict[int, str] = {}
    for g in games:
        if g["status"] not in TERMINAL_STATUSES:
            continue
        if g["home_score"] is None or g["away_score"] is None:
            continue
        winner = (g["home_team"] if g["home_score"] > g["away_score"]
                  else g["away_team"])
        winners_pair[(g["away_team"], g["home_team"])] = winner
        winners_pk[int(g["game_pk"])] = winner

    # ── Bet outcomes (existing behavior) ──────────────────────────────────
    if not picks_path.exists():
        log.warning("[outcomes] no picks file %s — skipping bet outcomes",
                    picks_path.name)
    else:
        picks = pd.read_csv(picks_path)
        if not picks.empty:
            rows = []
            for _, p in picks.iterrows():
                gid = int(p["game_id"])
                winner = winners_pk.get(gid)
                if winner is None:
                    outcome = "PENDING"
                elif winner == p["team"]:
                    outcome = "W"
                else:
                    outcome = "L"
                rows.append({
                    "game_id": gid, "team": p["team"], "side": p["side"],
                    "decimal": p["decimal"], "model_prob": p["model_prob"],
                    "edge_pp": p["edge_pp"], "tier": p["tier"],
                    "stake_u": p["stake_u"], "result": outcome,
                    "winner": winner,
                    "profit_u": (p["stake_u"] * (p["decimal"] - 1.0) if outcome == "W"
                                 else -p["stake_u"] if outcome == "L" else 0.0),
                })
            out_df = pd.DataFrame(rows)
            out_df.to_csv(outcomes_path, index=False)
            log.info("[outcomes] wrote %s (%d W / %d L / %d pending)",
                     outcomes_path.name,
                     (out_df["result"] == "W").sum(),
                     (out_df["result"] == "L").sum(),
                     (out_df["result"] == "PENDING").sum())
            update_tier_metrics(out_df, day)

    # ── SKIP outcomes (NEW) ──────────────────────────────────────────────
    # The audit has every game with the model's lean — including SKIPs.
    # Track whether the lean was right, bucketed by veto type. This is the
    # data we need to detect over-cautious vetoes.
    if not audit_path.exists():
        log.warning("[outcomes] no audit file %s — can't track skip outcomes",
                    audit_path.name)
        return
    audit = pd.read_csv(audit_path)
    skip_rows = []
    for _, a in audit.iterrows():
        if a["tier"] != "SKIP":
            continue
        winner = winners_pair.get((a["away"], a["home"]))
        if winner is None:
            result = "PENDING"
        elif winner == a["pick"]:
            result = "W"  # model's lean was right (we missed a winner)
        else:
            result = "L"  # model's lean was wrong (skip was correct)
        skip_rows.append({
            "away": a["away"], "home": a["home"], "pick": a["pick"],
            "pick_prob": a["pick_prob"], "veto_type": _classify_veto(a["notes"]),
            "winner": winner, "result": result,
        })
    if skip_rows:
        skip_df = pd.DataFrame(skip_rows)
        skip_df.to_csv(skipped_outcomes_path, index=False)
        n_w = (skip_df["result"] == "W").sum()
        n_l = (skip_df["result"] == "L").sum()
        log.info("[skip-outcomes] wrote %s — model's lean was right on "
                 "%d/%d skipped games (%.0f%%)", skipped_outcomes_path.name,
                 n_w, n_w + n_l, 100.0 * n_w / max(n_w + n_l, 1))
        update_veto_metrics(skip_df, day)

    # ── Signal-vs-model disagreement tracker (NEW) ───────────────────────
    # Compute each signal's vote per game and log when a 2+ signal majority
    # disagrees with the model's pick. After 14 days of data, we can tell
    # if the signals collectively beat the model on disagreement games
    # (would indicate we should add a soft-override rule).
    track_signal_disagreement(day, audit, winners_pair)


def track_signal_disagreement(day: date, audit, winners_pair) -> None:
    """Build slate features for `day`, compute each signal's team vote,
    and log every game where the signal-majority disagrees with the model
    pick. Writes per-game rows to outcomes_disagreement_YYYY-MM-DD.csv and
    rolls up to metrics/signal_vs_model_disagreement.csv.

    Decision rule: a 2+ signal majority is required (single-signal
    disagreements are noise). When 2+ signals point at one team and the
    model picks the OTHER team, log that game as a disagreement.

    After 14 days we'll know:
      - On disagreement games, who's more accurate (model or signals)?
      - If signals win >55% on disagreement games, add a soft override.
    """
    import pandas as pd
    try:
        sys.path.insert(0, str(ROOT))
        from mlb_edge.build_pipeline import build_slate_frame
        slate = build_slate_frame(day, include_weather=False)
    except Exception as e:
        log.warning("[disagreement] couldn't build slate features: %s", e)
        return
    if slate.empty:
        return

    rows = []
    for _, sf in slate.iterrows():
        a, h = sf["away_team"], sf["home_team"]
        winner = winners_pair.get((a, h))
        if winner is None:
            continue   # not settled

        # Model pick from audit
        audit_row = audit[(audit["away"] == a) & (audit["home"] == h)]
        if audit_row.empty:
            continue
        model_pick = audit_row.iloc[0]["pick"]

        # Compute per-signal team votes
        votes = _compute_signal_votes(sf)
        # Tally
        home_votes = sum(1 for v in votes.values() if v == "home")
        away_votes = sum(1 for v in votes.values() if v == "away")
        # Determine signal-majority team
        if home_votes >= 2 and home_votes > away_votes:
            sig_majority = h
        elif away_votes >= 2 and away_votes > home_votes:
            sig_majority = a
        else:
            sig_majority = None

        if sig_majority is None or sig_majority == model_pick:
            continue   # not a disagreement, skip

        # Disagreement! Record it.
        model_right = (model_pick == winner)
        sig_right = (sig_majority == winner)
        rows.append({
            "date": day.isoformat(),
            "away": a, "home": h,
            "model_pick": model_pick,
            "signal_majority_pick": sig_majority,
            "n_home_signals": home_votes,
            "n_away_signals": away_votes,
            "votes": ";".join(f"{k}={v}" for k, v in votes.items() if v),
            "winner": winner,
            "model_right": model_right,
            "signals_right": sig_right,
        })

    if not rows:
        log.info("[disagreement] no 2+ signal disagreements with model today")
        return

    dis_path = ROOT / f"outcomes_disagreement_{day:%Y-%m-%d}.csv"
    pd.DataFrame(rows).to_csv(dis_path, index=False)

    # Roll up to metrics
    metrics_path = METRICS / "signal_vs_model_disagreement.csv"
    daily = pd.DataFrame([{
        "date": day.isoformat(),
        "n_disagreements": len(rows),
        "model_wins": sum(1 for r in rows if r["model_right"]),
        "signals_win": sum(1 for r in rows if r["signals_right"]),
        "neither": sum(1 for r in rows if not r["model_right"] and not r["signals_right"]),
    }])
    if metrics_path.exists():
        existing = pd.read_csv(metrics_path)
        existing = existing[existing["date"] != day.isoformat()]
        combined = pd.concat([existing, daily], ignore_index=True)
    else:
        combined = daily
    combined.to_csv(metrics_path, index=False)

    log.info("[disagreement] %d games today: model %d, signals %d",
             len(rows), daily.iloc[0]["model_wins"], daily.iloc[0]["signals_win"])

    # Rolling 14-day check — flag if signals are systematically beating model
    if len(combined) >= 14:
        recent = combined.sort_values("date").tail(14)
        n_total = int(recent["n_disagreements"].sum())
        if n_total >= 20:
            sig_hit = recent["signals_win"].sum() / n_total
            if sig_hit > 0.55:
                flag_path = METRICS / "drift_flags.jsonl"
                flag = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "kind": "signals_beat_model",
                    "rolling_signals_hit_rate": round(sig_hit, 3),
                    "n_disagreement_games": n_total,
                    "suggestion": (
                        f"Signal majority beat model in {sig_hit:.1%} of "
                        f"{n_total} disagreement games over 14d. Consider "
                        f"adding soft-override: when 2+ signals disagree "
                        f"with model pick, demote tier or flip the pick."
                    ),
                }
                with flag_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(flag) + "\n")
                log.warning("[drift] signals beat model %.1f%% on %d "
                            "disagreement games — flagged",
                            sig_hit * 100, n_total)


def _compute_signal_votes(features) -> Dict[str, Optional[str]]:
    """For one game's feature row, return each signal's team vote
    ('home'/'away'/None). DIRECTIONAL mode — sample-size gates are NOT
    applied here because this is a diagnostic tracker, not a bet-firing
    filter. We want to capture as many directional data points as possible
    so the rolling analysis has enough samples. The conviction filter's
    own gates still apply when actually placing bets.

    Threshold-only voting:
      F1: |sp_xera_gap| >= 0.75
      F2: |team_woba_gap| >= 0.020
      F3: |swing_take_gap| >= 15
      F4: |sp_luck| >= 1.0 (either side, takes whichever is more extreme)
      F5: |bullpen_siera_gap| >= 0.40
    """
    votes: Dict[str, Optional[str]] = {}

    # F1: SP xera advantage. Positive gap = home SP better.
    f1 = features.get("sp_xera_gap", 0) or 0
    if abs(f1) >= 0.75:
        votes["F1"] = "home" if f1 > 0 else "away"
    else:
        votes["F1"] = None

    # F2: lineup xwoba gap. Positive = home lineup better.
    f2 = features.get("team_woba_gap", 0) or 0
    if abs(f2) >= 0.020:
        votes["F2"] = "home" if f2 > 0 else "away"
    else:
        votes["F2"] = None

    # F3: swing-take discipline gap. Positive = home better.
    f3 = features.get("swing_take_gap", 0) or 0
    if abs(f3) >= 15:
        votes["F3"] = "home" if f3 > 0 else "away"
    else:
        votes["F3"] = None

    # F4: SP luck regression. Pick whichever side has the more extreme
    # |luck| value (the most-likely-to-regress pitcher's team is favored
    # if they're unlucky, opposite if they're lucky).
    home_luck = features.get("home_sp_luck", 0) or 0
    away_luck = features.get("away_sp_luck", 0) or 0
    if abs(home_luck) >= 1.0 and abs(home_luck) >= abs(away_luck):
        votes["F4"] = "home" if home_luck > 0 else "away"
    elif abs(away_luck) >= 1.0:
        votes["F4"] = "away" if away_luck > 0 else "home"
    else:
        votes["F4"] = None

    # F5: bullpen advantage. Positive = home bullpen better.
    f5 = features.get("bullpen_siera_gap", 0) or 0
    if abs(f5) >= 0.40:
        votes["F5"] = "home" if f5 > 0 else "away"
    else:
        votes["F5"] = None

    return votes


def update_veto_metrics(skip_df, day: date) -> None:
    """Append per-veto-type SKIP outcomes to metrics/veto_performance.csv.

    Columns: date, veto_type, n, lean_wins, lean_losses, lean_hit_rate.

    `lean_hit_rate > 55%` over a 14-day rolling window means the veto is
    OVER-CAUTIOUS — we're skipping games the model would have correctly
    picked. That's a flag to tighten the veto threshold (e.g. raise the
    F1 negative-veto threshold from -0.75 → -1.00, requiring a more
    extreme opposing SP advantage before we kill the bet).
    """
    import pandas as pd

    metrics_path = METRICS / "veto_performance.csv"
    settled = skip_df[skip_df["result"].isin(["W", "L"])]
    if settled.empty:
        return

    by_type = settled.groupby("veto_type").agg(
        n=("result", "size"),
        lean_wins=("result", lambda s: (s == "W").sum()),
        lean_losses=("result", lambda s: (s == "L").sum()),
    ).reset_index()
    by_type["lean_hit_rate"] = (by_type["lean_wins"] / by_type["n"]).round(4)
    by_type.insert(0, "date", day.isoformat())

    if metrics_path.exists():
        existing = pd.read_csv(metrics_path)
        existing = existing[existing["date"] != day.isoformat()]
        combined = pd.concat([existing, by_type], ignore_index=True)
    else:
        combined = by_type
    combined.to_csv(metrics_path, index=False)
    log.info("[veto-metrics] updated veto_performance.csv with %d rows",
             len(by_type))

    # Drift check — if any veto type's lean has been winning >55% over the
    # last 14 days, the veto is over-cautious. Flag for review.
    if len(combined) >= 14:
        recent = combined.sort_values("date").tail(14 * 4)  # ~14 days × 4 veto types
        for veto in recent["veto_type"].unique():
            t = recent[recent["veto_type"] == veto]
            n_total = int(t["n"].sum())
            if n_total < 10:
                continue   # too small a sample
            hr = t["lean_wins"].sum() / max(n_total, 1)
            if hr > 0.55:
                flag_path = METRICS / "drift_flags.jsonl"
                flag = {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "kind": "veto_over_cautious",
                    "veto_type": veto,
                    "rolling_lean_hit_rate": round(hr, 3),
                    "n_skipped": n_total,
                    "suggestion": (
                        f"{veto} lean was right {hr:.1%} of the time across "
                        f"{n_total} skipped games — veto threshold may be "
                        f"too tight, consider relaxing"
                    ),
                }
                with flag_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(flag) + "\n")
                log.warning("[drift] %s lean hit rate %.1f%% — flagged",
                            veto, hr * 100)


def update_tier_metrics(out_df, day: date) -> None:
    """Append today's tier-level results to metrics/tier_performance.csv.
    This is the long-term self-improvement substrate: track hit rate and
    ROI by conviction tier so we can spot drift and tune thresholds.
    """
    import pandas as pd

    metrics_path = METRICS / "tier_performance.csv"
    settled = out_df[out_df["result"].isin(["W", "L"])]
    if settled.empty:
        return

    by_tier = settled.groupby("tier").agg(
        n=("result", "size"),
        wins=("result", lambda s: (s == "W").sum()),
        losses=("result", lambda s: (s == "L").sum()),
        total_staked=("stake_u", "sum"),
        total_profit=("profit_u", "sum"),
    ).reset_index()
    by_tier["hit_rate"] = (by_tier["wins"] / by_tier["n"]).round(4)
    by_tier["roi"] = (by_tier["total_profit"] / by_tier["total_staked"]).round(4)
    by_tier.insert(0, "date", day.isoformat())

    if metrics_path.exists():
        existing = pd.read_csv(metrics_path)
        # Drop any prior entry for today (idempotent)
        existing = existing[existing["date"] != day.isoformat()]
        combined = pd.concat([existing, by_tier], ignore_index=True)
    else:
        combined = by_tier
    combined.to_csv(metrics_path, index=False)
    log.info("[metrics] updated tier_performance.csv with %d tier rows",
             len(by_tier))

    # Self-improvement watch — flag drift if PLATINUM/DIAMOND hit rate drops
    # below 0.50 over a 14-day rolling window.
    if len(combined) >= 14:
        recent = combined.sort_values("date").tail(14)
        for tier in ("PLATINUM", "DIAMOND"):
            t = recent[recent["tier"] == tier]
            if len(t) >= 5:
                hr = t["wins"].sum() / max(t["n"].sum(), 1)
                if hr < 0.50:
                    flag_path = METRICS / "drift_flags.jsonl"
                    flag = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "tier": tier,
                        "rolling_14d_hit_rate": round(hr, 3),
                        "n_bets": int(t["n"].sum()),
                        "suggestion": f"{tier} hit rate {hr:.1%} < 50% — "
                                      "consider tightening conviction thresholds",
                    }
                    with flag_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(flag) + "\n")
                    log.warning("[drift] %s 14d hit rate %.1f%% — flagged",
                                tier, hr * 100)


def loop_once(day: date, state: Dict) -> Dict:
    """One iteration of the watch loop. Returns updated state."""
    games = fetch_schedule(day)
    if not games:
        log.warning("[poll] no games returned for %s", day)
        return state

    processed = set(state.get("processed_game_pks", []))
    fail_counts: dict = state.setdefault("fail_counts", {})
    now_done = []
    n_terminal = 0
    n_postponed = 0
    MAX_RECAL_RETRIES = 3   # cap retries on persistent failures

    for g in games:
        if g["status"] in TERMINAL_STATUSES:
            n_terminal += 1
            if g["game_pk"] in processed:
                continue
            pk_str = str(g["game_pk"])
            if fail_counts.get(pk_str, 0) >= MAX_RECAL_RETRIES:
                log.warning("[end-detected] game_pk=%d hit max retries (%d) — "
                            "marking processed without recalibration",
                            g["game_pk"], MAX_RECAL_RETRIES)
                now_done.append(g["game_pk"])
                continue
            log.info("[end-detected] game_pk=%d %s @ %s  %d-%d  status=%s",
                     g["game_pk"], g["away_team"], g["home_team"],
                     g["away_score"] or 0, g["home_score"] or 0,
                     g["status"])
            ok = trigger_recalibration(g["game_pk"])
            queue_chrome_scrape(g["game_pk"], g["away_team"], g["home_team"], day)
            if ok:
                now_done.append(g["game_pk"])
                fail_counts.pop(pk_str, None)
            else:
                # Bug #4 fix: bound retries so a persistent failure can't
                # hammer external APIs every 5 min indefinitely. After
                # MAX_RECAL_RETRIES attempts we give up on this game's
                # recalibration but still let the day finish (outcomes
                # tracker doesn't need a recalibration to log results).
                fail_counts[pk_str] = fail_counts.get(pk_str, 0) + 1
                log.warning("[recalibrate] game_pk=%d failure %d/%d",
                            g["game_pk"], fail_counts[pk_str], MAX_RECAL_RETRIES)
        elif g["status"] in POSTPONED_STATUSES:
            n_postponed += 1
            if g["game_pk"] not in processed:
                now_done.append(g["game_pk"])  # mark to skip in future polls

    state["processed_game_pks"] = sorted(processed | set(now_done))
    state["fail_counts"] = fail_counts

    log.info("[poll] %d/%d terminal, %d postponed, %d already-processed",
             n_terminal, len(games), n_postponed, len(state["processed_game_pks"]))

    # End-of-day handling
    all_done = (n_terminal + n_postponed) == len(games) and len(games) > 0
    if all_done and not state.get("end_of_day_done"):
        log.info("[end-of-day] all games complete — running outcomes tracker")
        write_outcomes(day, games)
        state["end_of_day_done"] = True

    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="Run one poll cycle then exit (for testing).")
    ap.add_argument("--date", type=lambda s: date.fromisoformat(s),
                    help="Override day (default: today).")
    args = ap.parse_args()

    day = args.date or date.today()
    log.info("=" * 60)
    log.info("game_end_watcher: ENTER (day=%s, poll=%ds)", day, POLL_INTERVAL_S)
    log.info("=" * 60)

    state = load_state(day)

    while True:
        try:
            state = loop_once(day, state)
            save_state(day, state)
        except KeyboardInterrupt:
            log.info("interrupted — exiting")
            return 0
        except Exception as e:
            log.exception("loop_once crashed: %s", e)

        if args.once:
            return 0

        # If end of day done, sleep until tomorrow's noon-ish before resuming.
        # Bug fix: clamp the wait to a sane MIN/MAX range so a clock skew or
        # naive-datetime bug can't either (a) sleep 60s in a tight loop or
        # (b) sleep multiple days. We also re-derive `day` after the sleep
        # in case the system date crossed midnight while we slept.
        if state.get("end_of_day_done"):
            tomorrow = day + timedelta(days=1)
            tomorrow_noon = datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=12)
            secs_raw = (tomorrow_noon - datetime.now()).total_seconds()
            # Clamp: at least 30 min (so we don't tight-loop on clock skew),
            # at most 18 hr (so we don't oversleep into the next slate).
            secs = max(1800, min(secs_raw, 18 * 3600))
            log.info("[idle] end of day done; sleeping %.1fh until tomorrow noon "
                     "(raw=%.1fh)", secs / 3600, secs_raw / 3600)
            time.sleep(secs)
            day = date.today()
            state = load_state(day)
            continue

        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
