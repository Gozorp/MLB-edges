"""
recalibrate_intraday.py
-----------------------
Intraday recalibration triggered when a game ends. Refreshes Savant CSVs
(direct HTTP — no Chrome needed; the just-ended game's at-bats now show up
in team / pitcher aggregates), wipes the current-season feature cache,
re-runs predict for today's slate, and writes a diff vs the most recent
picks file.

Output:
  - picks_YYYYMMDD.csv             — overwritten with refreshed picks
  - picks_YYYYMMDD_pregame{pk}.csv — snapshot of picks BEFORE this recalibration
  - recalibration_log_YYYYMMDD.jsonl — append-only log of every recalibration:
        {ts, trigger_game_pk, snapshot_path, picks_diff: {...}}

Usage:
    python scripts/recalibrate_intraday.py --game-pk 824851
    python scripts/recalibrate_intraday.py --game-pk 824851 --skip-savant
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)

today = date.today()
LOG_FILE = LOGS / f"recalibrate_intraday_{today:%Y%m%d}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("recalibrate_intraday")


def _refresh_script_path(name: str) -> Path | None:
    """refresh_*.py lives in scripts/ — historically at the repo root
    (D:/mlb_edge/scripts/) but newer copies may also live in the project
    subdir. Check both, mirroring auto_runner.py's fallback."""
    candidates = [
        ROOT / "scripts" / name,
        ROOT.parent / "scripts" / name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


# Windows-only kwargs for hidden console window. `creationflags` is a
# Windows-only keyword arg — passing it on Linux/macOS raises TypeError
# even with value 0, so we keep it out of kwargs entirely off-Windows.
_WIN_HIDDEN_KW: dict = (
    {"creationflags": 0x08000000} if sys.platform == "win32" else {}
)


def step_savant() -> bool:
    log.info("[savant] refresh START")
    script = _refresh_script_path("refresh_savant.py")
    if script is None:
        log.error("[savant] script not found in scripts/ or ../scripts/")
        return False
    try:
        p = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT, capture_output=True, text=True, timeout=600,
            **_WIN_HIDDEN_KW,
        )
        if p.returncode != 0:
            log.error("[savant] FAIL rc=%d: %s", p.returncode, p.stderr[-400:])
            return False
        log.info("[savant] OK")
        return True
    except Exception as e:
        log.error("[savant] EXCEPTION %s", e)
        return False


def step_wipe_current_season_cache() -> int:
    """Wipe v11 cache files for the CURRENT season only. Past seasons are
    immutable (their `through` date is well after season end), so keeping
    those caches saves ~2.5 hr of rebuild on each recalibration.
    """
    cache_dir = ROOT / "data" / "feature_cache"
    if not cache_dir.exists():
        return 0
    pattern = f"features_{today.year}_*_v11.parquet"
    deleted = 0
    for f in cache_dir.glob(pattern):
        try:
            f.unlink()
            deleted += 1
            log.info("[cache] wiped %s", f.name)
        except Exception as e:
            log.warning("[cache] couldn't wipe %s: %s", f.name, e)
    return deleted


def step_snapshot_current_picks(trigger_pk: int) -> Path | None:
    """Save the current picks_YYYYMMDD.csv as a pre-game snapshot."""
    src = ROOT / f"picks_{today:%Y-%m-%d}.csv"
    if not src.exists():
        log.warning("[snapshot] no current picks file to snapshot")
        return None
    snap = ROOT / f"picks_{today:%Y-%m-%d}_pregame{trigger_pk}.csv"
    shutil.copy2(src, snap)
    log.info("[snapshot] saved %s", snap.name)
    return snap


def step_predict() -> bool:
    out = ROOT / f"picks_{today:%Y-%m-%d}.csv"
    log.info("[predict] START")
    try:
        p = subprocess.run(
            [sys.executable, "-m", "mlb_edge.main",
             "--mode", "predict",
             "--date", today.isoformat(),
             "--model_path", "models/latest.pkl",
             "--out", str(out),
             "--bankroll", "100"],
            cwd=ROOT, capture_output=True, text=True, timeout=900,
            **_WIN_HIDDEN_KW,
        )
        if p.returncode != 0:
            log.error("[predict] FAIL rc=%d: %s", p.returncode, p.stderr[-600:])
            return False
        # Tail of stdout shows bet sheet
        tail = "\n".join(p.stdout.strip().splitlines()[-10:])
        log.info("[predict] OK\n%s", tail)
        return True
    except Exception as e:
        log.error("[predict] EXCEPTION %s", e)
        return False


def step_diff_picks(snapshot: Path | None) -> dict:
    """Compare new picks against snapshot. Returns a dict describing the diff."""
    import pandas as pd

    new_path = ROOT / f"picks_{today:%Y-%m-%d}.csv"
    if not new_path.exists():
        return {"error": "no new picks file"}
    new_df = pd.read_csv(new_path)

    if snapshot is None or not snapshot.exists():
        return {
            "snapshot": None,
            "new_bets": new_df.to_dict("records"),
            "first_picks_today": True,
        }
    old_df = pd.read_csv(snapshot)

    old_keys = set(old_df["game_id"].astype(int))
    new_keys = set(new_df["game_id"].astype(int))
    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)

    # For games in both, check tier / edge / model_prob changes
    common = sorted(old_keys & new_keys)
    flips = []
    for gid in common:
        o = old_df[old_df["game_id"] == gid].iloc[0]
        n = new_df[new_df["game_id"] == gid].iloc[0]
        if (o["team"] != n["team"] or o["tier"] != n["tier"] or
                abs(float(o["model_prob"]) - float(n["model_prob"])) >= 0.01):
            flips.append({
                "game_id": int(gid),
                "old": {"team": str(o["team"]), "tier": str(o["tier"]),
                        "model_prob": float(o["model_prob"]),
                        "edge_pp": float(o["edge_pp"])},
                "new": {"team": str(n["team"]), "tier": str(n["tier"]),
                        "model_prob": float(n["model_prob"]),
                        "edge_pp": float(n["edge_pp"])},
            })

    return {
        "snapshot": snapshot.name,
        "added_bets": added,
        "removed_bets": removed,
        "flipped": flips,
        "new_total_bets": len(new_df),
        "old_total_bets": len(old_df),
    }


def append_recalibration_log(trigger_pk: int, diff: dict) -> None:
    log_path = ROOT / f"recalibration_log_{today:%Y%m%d}.jsonl"
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "trigger_game_pk": trigger_pk,
        "diff": diff,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    log.info("[log] appended to %s", log_path.name)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-pk", type=int, required=True,
                    help="game_pk that just ended (for the diff log)")
    ap.add_argument("--skip-savant", action="store_true",
                    help="Skip Savant refresh (use cached). For debugging.")
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("RECALIBRATE INTRADAY: trigger game_pk=%d", args.game_pk)
    log.info("=" * 60)

    if not args.skip_savant:
        if not step_savant():
            return 1

    snapshot = step_snapshot_current_picks(args.game_pk)
    n_wiped = step_wipe_current_season_cache()
    log.info("[cache] wiped %d current-season cache files", n_wiped)

    if not step_predict():
        return 1

    diff = step_diff_picks(snapshot)
    append_recalibration_log(args.game_pk, diff)

    log.info("DIFF SUMMARY:")
    log.info("  Bet sheet: %d -> %d bets",
             diff.get("old_total_bets", 0), diff.get("new_total_bets", 0))
    log.info("  Added: %s", diff.get("added_bets", []))
    log.info("  Removed: %s", diff.get("removed_bets", []))
    if diff.get("flipped"):
        log.info("  Flipped: %d games", len(diff["flipped"]))
        for f in diff["flipped"]:
            log.info("    %s: %s %s %.3f -> %s %s %.3f",
                     f["game_id"],
                     f["old"]["team"], f["old"]["tier"], f["old"]["model_prob"],
                     f["new"]["team"], f["new"]["tier"], f["new"]["model_prob"])
    log.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
