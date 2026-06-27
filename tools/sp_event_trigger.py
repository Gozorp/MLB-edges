# -*- coding: utf-8 -*-
"""
sp_event_trigger.py -- event-driven Starting-Pitcher watcher for the MLB-edge slate.

WHAT IT DOES
  Polls the MLB Stats API schedule endpoint for a slate's probable pitchers, keeps a
  per-game fingerprint of the SP state, and fires a re-run of the (frozen) prediction
  pipeline the moment a probable changes. It detects BOTH transitions:
    * CONFIRMED   - a game that was TBD (no probable) now has a pitcher announced
    * SWAPPED     - an already-announced probable is replaced by a different pitcher
    * (also flags SCRATCHED - a confirmed probable reverting to TBD, e.g. a late scratch)

DESIGN (why it's built this way)
  - State-diff, not text-parsing. Each poll builds {GAMEKEY -> SpState(away_id, home_id,...)}.
    The trigger compares it to the last *successfully processed* state persisted on disk.
    Diffing real pitcher IDs is what lets it catch swaps, which a "was it pending?" check can't.
  - Edge-triggered + coalesced. It acts only on a CHANGE, and one poll that changed N games
    triggers ONE slate rebuild (the rebuild re-scores every game), then records the new state
    so it never re-fires for the same change.
  - Fail-safe on a flaky API. Fetch uses retry+backoff; if the poll ultimately fails, the tick
    is abandoned WITHOUT touching saved state -- a transient timeout can never be misread as
    "everyone went back to TBD" and trigger a bogus rebuild. (This matters: statsapi degrades
    at off-hours.)
  - Guards for unattended running: act-only time window, single-instance lock, a daily rebuild
    cap (a flapping feed can't trigger 50 rebuilds), and a minimum interval between rebuilds.
  - Freeze-safe. The re-run hook calls the FROZEN slate chain (run_local_slate + publish_local);
    it never touches model weights, calibration, config, or stakes. The pitcher's data flows in
    only as a fresh model INPUT.

RUN MODES
  * Loop (default):   python tools/sp_event_trigger.py --loop            # long-running poller
  * One tick (cron):  python tools/sp_event_trigger.py --once            # check once, exit
  Either way each tick is self-contained and crash-safe (state on disk; next tick recovers).

USAGE
  python tools/sp_event_trigger.py [YYYY-MM-DD] [--loop|--once] [--interval 300]
                                    [--dry-run] [--no-publish]
  Default date = today (local). Default mode = --loop. Default interval = 300s.
"""
from __future__ import annotations
import argparse
import dataclasses
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://statsapi.mlb.com/api/v1"
UA = {"User-Agent": "mlb_edge-sp-event-trigger/1.0"}
STATE_DIR = os.path.join(ROOT, "data", "state")
LOCK = os.path.join(STATE_DIR, "sp_event_trigger.lock")
PY = sys.executable

# --- tunables (safe defaults for unattended operation) ----------------------------------
POLL_INTERVAL_S = 300          # how often to poll in --loop mode
ACT_WINDOW = (6, 23)           # only ACT on changes during these local hours (inclusive)
FETCH_TIMEOUT_S = 20
FETCH_RETRIES = 3
FETCH_BACKOFF_S = 1.5          # exponential: 1.5, 3.0, 6.0 ...
LOCK_TTL_S = 900               # a rebuild in flight blocks new ticks for at most this long
DAILY_REBUILD_CAP = 8          # max auto-rebuilds per slate-date
MIN_SECONDS_BETWEEN_REBUILDS = 120

# Canonicalize team-abbreviation variants so a game key is stable across feeds.
CANON = {"CHW": "CWS", "ARI": "AZ", "OAK": "ATH", "WSN": "WSH",
         "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}


def canon(x) -> str:
    s = str(x or "").strip().upper()
    return CANON.get(s, s)


def log(msg: str) -> None:
    print("[sp-trigger %s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------------------------------
# SP state model
# ---------------------------------------------------------------------------------------
@dataclasses.dataclass(frozen=True)
class SpState:
    away_id: int | None
    away_name: str
    home_id: int | None
    home_name: str

    def fingerprint(self) -> str:
        # IDs are the source of truth for "did the pitcher change"; names are for humans.
        return "%s|%s" % (self.away_id or 0, self.home_id or 0)


def fetch_probables(date: str) -> dict[str, SpState]:
    """One schedule call -> {GAMEKEY: SpState}. Raises on total failure (caller skips tick)."""
    url = "%s/schedule?sportId=1&date=%s&hydrate=probablePitcher,team" % (API, date)
    last_err = None
    for attempt in range(FETCH_RETRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:
                payload = json.load(r)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
            last_err = e
            wait = FETCH_BACKOFF_S * (2 ** attempt)
            log("fetch attempt %d/%d failed (%s); backoff %.1fs" % (attempt + 1, FETCH_RETRIES, e, wait))
            time.sleep(wait)
    else:
        raise RuntimeError("schedule fetch failed after %d attempts: %s" % (FETCH_RETRIES, last_err))

    out: dict[str, SpState] = {}
    for d in payload.get("dates", []):
        for g in d.get("games", []):
            # skip non-counting states that don't have a meaningful probable yet
            t = g.get("teams", {})
            away, home = t.get("away", {}), t.get("home", {})
            ak = canon((away.get("team") or {}).get("abbreviation"))
            hk = canon((home.get("team") or {}).get("abbreviation"))
            if not ak or not hk:
                continue
            # doubleheaders: disambiguate by gameNumber so G1/G2 are distinct keys
            gnum = g.get("gameNumber", 1)
            key = "%s@%s" % (ak, hk) if gnum == 1 else "%s@%s#G%d" % (ak, hk, gnum)
            ap = away.get("probablePitcher") or {}
            hp = home.get("probablePitcher") or {}
            out[key] = SpState(
                away_id=ap.get("id"), away_name=ap.get("fullName", "TBD"),
                home_id=hp.get("id"), home_name=hp.get("fullName", "TBD"),
            )
    return out


# ---------------------------------------------------------------------------------------
# Event detection
# ---------------------------------------------------------------------------------------
@dataclasses.dataclass
class Event:
    game: str
    kind: str      # confirmed | swapped | scratched | new_game
    detail: str


def detect_events(prev: dict[str, dict], curr: dict[str, SpState]) -> list[Event]:
    """Compare the last processed state to the current poll; emit one Event per real change."""
    events: list[Event] = []
    for key, cur in curr.items():
        old = prev.get(key)
        if old is None:
            # game not seen before; only noteworthy if it ALREADY has a probable
            if cur.away_id or cur.home_id:
                events.append(Event(key, "new_game", "appeared with %s / %s" % (cur.away_name, cur.home_name)))
            continue
        for side, oid, nid, oname, nname in (
            ("away", old.get("away_id"), cur.away_id, old.get("away_name", "TBD"), cur.away_name),
            ("home", old.get("home_id"), cur.home_id, old.get("home_name", "TBD"), cur.home_name),
        ):
            if oid == nid:
                continue
            if not oid and nid:
                events.append(Event(key, "confirmed", "%s SP confirmed: TBD -> %s" % (side, nname)))
            elif oid and not nid:
                events.append(Event(key, "scratched", "%s SP scratched: %s -> TBD" % (side, oname)))
            elif oid and nid:
                events.append(Event(key, "swapped", "%s SP swapped: %s -> %s" % (side, oname, nname)))
    return events


# ---------------------------------------------------------------------------------------
# The re-run hook (decoupled so it's easy to swap). Freeze-safe.
# ---------------------------------------------------------------------------------------
def rerun_model(date: str, publish: bool) -> bool:
    """Re-run the FROZEN slate pipeline for `date`, optionally publishing. Returns success."""
    try:
        log("re-running frozen slate for %s ..." % date)
        r1 = subprocess.run([PY, os.path.join("tools", "run_local_slate.py"), date],
                            cwd=ROOT, timeout=1800)
        if r1.returncode != 0:
            log("run_local_slate returned %d -> NOT publishing" % r1.returncode)
            return False
        if publish:
            subprocess.run([PY, os.path.join("tools", "publish_local.py"), "nightly"],
                           cwd=ROOT, timeout=600)
        log("re-run complete (%s)" % ("published" if publish else "local only"))
        return True
    except subprocess.TimeoutExpired:
        log("re-run TIMED OUT")
        return False
    except Exception as e:  # noqa: BLE001
        log("re-run error: %s" % e)
        return False


# ---------------------------------------------------------------------------------------
# State + guards
# ---------------------------------------------------------------------------------------
def state_path(date: str) -> str:
    return os.path.join(STATE_DIR, "sp_event_state_%s.json" % date)


def load_state(date: str) -> dict:
    try:
        with open(state_path(date), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"games": {}, "rebuilds": 0, "last_rebuild_ts": 0}


def save_state(date: str, games: dict[str, SpState], meta: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    blob = {
        "games": {k: dataclasses.asdict(v) for k, v in games.items()},
        "rebuilds": meta.get("rebuilds", 0),
        "last_rebuild_ts": meta.get("last_rebuild_ts", 0),
        "updated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    tmp = state_path(date) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, indent=1)
    os.replace(tmp, state_path(date))  # atomic


def lock_held() -> bool:
    return os.path.exists(LOCK) and (time.time() - os.path.getmtime(LOCK)) < LOCK_TTL_S


def acquire_lock() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK, "w") as f:
        f.write("%d %s" % (os.getpid(), dt.datetime.now().isoformat()))


def release_lock() -> None:
    try:
        os.remove(LOCK)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------
# One tick
# ---------------------------------------------------------------------------------------
def tick(date: str, dry: bool, publish: bool) -> None:
    now = dt.datetime.now()
    st = load_state(date)
    prev_games = st.get("games", {})

    # 1) POLL (resilient). On hard failure, abandon the tick WITHOUT mutating state.
    try:
        curr = fetch_probables(date)
    except Exception as e:  # noqa: BLE001
        log("poll failed, keeping last state: %s" % e)
        return

    # 2) DIFF
    events = detect_events(prev_games, curr)
    if not events:
        log("%s: no SP changes (%d games tracked)" % (date, len(curr)))
        # refresh stored snapshot so first-seen games become the baseline (no rebuild)
        if not prev_games:
            save_state(date, curr, st)
        return

    for ev in events:
        log("CHANGE [%s] %s -- %s" % (ev.kind, ev.game, ev.detail))

    # 3) DECIDE (guards)
    actionable = [e for e in events if e.kind in ("confirmed", "swapped", "new_game")]
    if not actionable:
        log("only non-actionable events (e.g. scratch->TBD); recording, no rebuild")
        save_state(date, curr, st)
        return
    if dry:
        log("--dry-run: WOULD rebuild %s for %d change(s)" % (date, len(actionable)))
        return
    if not (ACT_WINDOW[0] <= now.hour <= ACT_WINDOW[1]):
        log("outside act-window %s (hour %02d) -> defer (state NOT advanced; will fire in-window)"
            % (ACT_WINDOW, now.hour))
        return
    if st.get("rebuilds", 0) >= DAILY_REBUILD_CAP:
        log("daily rebuild cap (%d) reached -> recording state, no rebuild" % DAILY_REBUILD_CAP)
        save_state(date, curr, st)
        return
    if time.time() - st.get("last_rebuild_ts", 0) < MIN_SECONDS_BETWEEN_REBUILDS:
        log("min interval between rebuilds not elapsed -> defer to next tick")
        return
    if lock_held():
        log("another rebuild in flight (lock) -> defer")
        return

    # 4) ACT (coalesced: one rebuild covers all changed games). Update state only on success.
    acquire_lock()
    try:
        ok = rerun_model(date, publish=publish)
    finally:
        release_lock()
    if ok:
        st["rebuilds"] = st.get("rebuilds", 0) + 1
        st["last_rebuild_ts"] = time.time()
        save_state(date, curr, st)
        log("rebuild #%d/%d done for %s; state advanced" % (st["rebuilds"], DAILY_REBUILD_CAP, date))
    else:
        log("rebuild failed -> state NOT advanced; will retry on next tick")


# ---------------------------------------------------------------------------------------
# CLI / loop
# ---------------------------------------------------------------------------------------
_STOP = False


def _handle_signal(signum, _frame):
    global _STOP
    _STOP = True
    log("signal %d received -> will stop after current tick" % signum)


def main() -> int:
    ap = argparse.ArgumentParser(description="Event-driven SP-change watcher for the MLB-edge slate.")
    ap.add_argument("date", nargs="?", default=dt.date.today().isoformat(),
                    help="slate date YYYY-MM-DD (default: today, local)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--loop", action="store_true", help="long-running poller (default)")
    mode.add_argument("--once", action="store_true", help="single tick then exit (for cron/schtasks)")
    ap.add_argument("--interval", type=int, default=POLL_INTERVAL_S, help="poll interval seconds (loop mode)")
    ap.add_argument("--dry-run", action="store_true", help="detect + log only; never rebuild")
    ap.add_argument("--no-publish", action="store_true", help="rebuild locally but do not git-publish")
    args = ap.parse_args()
    publish = not args.no_publish

    if args.once:
        tick(args.date, dry=args.dry_run, publish=publish)
        return 0

    # default: loop
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass
    log("starting loop: date=%s interval=%ds dry=%s publish=%s"
        % (args.date, args.interval, args.dry_run, publish))
    while not _STOP:
        try:
            tick(args.date, dry=args.dry_run, publish=publish)
        except Exception as e:  # noqa: BLE001  (one bad tick must never kill the loop)
            log("tick crashed (continuing): %s" % e)
        for _ in range(args.interval):
            if _STOP:
                break
            time.sleep(1)
    log("stopped cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
