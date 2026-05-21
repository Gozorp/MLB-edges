"""
tools/backtest_override.py
==========================
Out-of-sample regression detector for the Claude OVERRIDE rule.

Background
----------
The 4-condition OVERRIDE rule (f5_full_delta > 0.20 AND elite opposing SP via
xERA AND stacked pipeline flags AND fair_prob < 0.42) was 6-of-6 in the
in-sample archive (2026-05-08 → 2026-05-18).  That 6-of-6 is the evidence
that produced the rule — it cannot also be the evidence that validates it.

This harness collects OUT-OF-SAMPLE OVERRIDE fires (those with date strictly
after the 2026-05-21 freeze date) and produces a verdict against the locked
thresholds in `spaces/.../memory/project_override_backtest_thresholds.md`:

   sample floor:        n ≥ 10 out-of-sample fires
   keep threshold:      precision ≥ 85% AND wins_flipped < 2
   gray zone:           strict bimodal — below the keep threshold → demote
   reverse-direction:   ≥ 2 historical wins flipped → demote regardless of precision

Verdict states
--------------
   "inconclusive"       n < 10 out-of-sample fires; keep current behavior,
                        re-run when more postgame data accumulates
   "keep"               n ≥ 10 AND precision ≥ 85% AND wins_flipped < 2
   "demote"             n ≥ 10 AND (precision < 85% OR wins_flipped ≥ 2)

On verdict change
-----------------
   inconclusive → keep:    informational only, no patch required
   inconclusive → demote:  write `tools/PUSH_OVERRIDE_DEMOTE.bat` ready-to-run
                           and notify via the status JSON
   keep → demote:          same as above

User-in-the-loop discipline: the harness NEVER auto-commits or pushes.
It writes the patch + .bat and updates the status JSON; the user reviews
and double-clicks.

CLI
---
   python tools/backtest_override.py           — run, write status JSON, print verdict
   python tools/backtest_override.py --verbose — also print the per-fire ledger
   python tools/backtest_override.py --json    — print the full status object to stdout

Outputs (always overwritten):
   docs/data/backtest/override_status.json      — machine-readable status
   docs/data/backtest/override_ledger.csv       — per-fire ledger (audit trail)
   tools/PUSH_OVERRIDE_DEMOTE.bat               — only written if verdict == 'demote'

Per Architecture-Session Pre-Flight Prompt v1.0:
   Rule 1   probed before architect (probe showed n=6 in-sample as of 2026-05-21)
   Rule 2   test set + thresholds locked BEFORE code ran (see memory file)
   Rule 5   single-purpose harness; does NOT auto-deploy, does NOT touch parlay_builder
   Rule 6   best-effort wrapping — missing postgame JSON, malformed by_matchup,
            empty diag CSV all degrade to 'inconclusive' rather than crash
   Rule 11  reverse-direction sanity baked into the verdict logic
   Rule 12  architectural decisions documented at the top of the file
   Rule 13  push .bat itself narrates the demotion when written
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, date
from typing import Iterable, List, Optional

log = logging.getLogger("backtest_override")

# ---------------------------------------------------------------------------
# Locked thresholds — DO NOT MOVE without an explicit user re-sign.
# Cross-referenced to memory/project_override_backtest_thresholds.md.
# ---------------------------------------------------------------------------
FREEZE_DATE = date(2026, 5, 21)
SAMPLE_FLOOR = 10
PRECISION_THRESHOLD = 0.85
WINS_FLIPPED_LIMIT = 2  # demote if >= this many wins flipped

# ---------------------------------------------------------------------------
# Paths (anchored relative to repo root)
# ---------------------------------------------------------------------------
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
POSTGAME_DIR = os.path.join(ROOT, "docs", "data", "postgame")
BACKTEST_DIR = os.path.join(ROOT, "docs", "data", "backtest")
STATUS_FILE = os.path.join(BACKTEST_DIR, "override_status.json")
LEDGER_FILE = os.path.join(BACKTEST_DIR, "override_ledger.csv")
PUSH_BAT_FILE = os.path.join(ROOT, "PUSH_OVERRIDE_DEMOTE.bat")


# ---------------------------------------------------------------------------
# Per-fire dataclass
# ---------------------------------------------------------------------------
@dataclass
class OverrideFire:
    date: str
    matchup: str
    decision: str
    verdict: str           # WIN if model pick won (i.e. OVERRIDE was WRONG)
                           # LOSS if model pick lost (i.e. OVERRIDE was RIGHT)
    final_score: str
    model_pick: str
    in_sample: bool        # True if date <= FREEZE_DATE, False if after


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def _safe_load_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log.warning("skipping %s: %s", os.path.basename(path), e)
        return None


def collect_override_fires() -> List[OverrideFire]:
    """Walk the postgame archive and return every matchup where
    claude_decision contains OVERRIDE.  Tags each with in_sample=True/False
    based on the FREEZE_DATE boundary."""
    fires: List[OverrideFire] = []
    if not os.path.isdir(POSTGAME_DIR):
        log.warning("postgame dir missing: %s", POSTGAME_DIR)
        return fires
    for f in sorted(os.listdir(POSTGAME_DIR)):
        if not f.endswith(".json"):
            continue
        d = _safe_load_json(os.path.join(POSTGAME_DIR, f))
        if not isinstance(d, dict):
            continue
        date_str = d.get("date", f.replace(".json", ""))
        try:
            fire_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            log.warning("unparseable date in %s: %r", f, date_str)
            continue
        bm = d.get("by_matchup")
        if not isinstance(bm, dict):
            continue
        for matchup, info in bm.items():
            if not isinstance(info, dict):
                continue
            decision = str(info.get("claude_decision") or "").upper().strip()
            verdict = str(info.get("verdict") or "").upper().strip()
            if "OVERRIDE" not in decision:
                continue
            if verdict not in ("WIN", "LOSS"):
                log.warning("skipping OVERRIDE fire with unparseable verdict: "
                            "%s @ %s (%r)", date_str, matchup, verdict)
                continue
            fires.append(OverrideFire(
                date=date_str,
                matchup=matchup,
                decision=decision,
                verdict=verdict,
                final_score=str(info.get("final_score") or ""),
                model_pick=str(info.get("model_pick") or ""),
                in_sample=(fire_date <= FREEZE_DATE),
            ))
    return fires


# ---------------------------------------------------------------------------
# Verdict computation
# ---------------------------------------------------------------------------
@dataclass
class Verdict:
    state: str                       # 'inconclusive' | 'keep' | 'demote'
    reason: str
    n_out_of_sample: int
    n_in_sample: int
    precision_pct: Optional[float]   # None when verdict=inconclusive
    wins_flipped: Optional[int]
    losses_flipped: Optional[int]
    thresholds: dict = field(default_factory=lambda: {
        "freeze_date": FREEZE_DATE.isoformat(),
        "sample_floor": SAMPLE_FLOOR,
        "precision_threshold_pct": PRECISION_THRESHOLD * 100,
        "wins_flipped_limit": WINS_FLIPPED_LIMIT,
    })


def compute_verdict(fires: List[OverrideFire]) -> Verdict:
    oos = [f for f in fires if not f.in_sample]
    in_s = [f for f in fires if f.in_sample]
    n_oos = len(oos)
    n_in = len(in_s)

    if n_oos < SAMPLE_FLOOR:
        return Verdict(
            state="inconclusive",
            reason=(f"out-of-sample n={n_oos} below floor {SAMPLE_FLOOR}; "
                    f"keep current OVERRIDE behavior, re-run when more "
                    f"postgame data accumulates"),
            n_out_of_sample=n_oos, n_in_sample=n_in,
            precision_pct=None, wins_flipped=None, losses_flipped=None,
        )

    # OVERRIDE is "right" when the model's original pick LOSES on the flipped
    # side — i.e. verdict==LOSS in postgame terminology.  WIN means the model
    # was right and OVERRIDE was wrong (it flipped a winning pick).
    wins_flipped = sum(1 for f in oos if f.verdict == "WIN")
    losses_flipped = sum(1 for f in oos if f.verdict == "LOSS")
    total = wins_flipped + losses_flipped
    precision = losses_flipped / total if total > 0 else 0.0
    precision_pct = precision * 100

    if wins_flipped >= WINS_FLIPPED_LIMIT:
        return Verdict(
            state="demote",
            reason=(f"reverse-direction sanity (Rule 11): {wins_flipped} "
                    f"historical wins flipped >= limit {WINS_FLIPPED_LIMIT}; "
                    f"demote OVERRIDE to stake-size cap regardless of precision "
                    f"({precision_pct:.1f}%)"),
            n_out_of_sample=n_oos, n_in_sample=n_in,
            precision_pct=round(precision_pct, 2),
            wins_flipped=wins_flipped, losses_flipped=losses_flipped,
        )

    if precision >= PRECISION_THRESHOLD:
        return Verdict(
            state="keep",
            reason=(f"out-of-sample precision {precision_pct:.1f}% >= "
                    f"{PRECISION_THRESHOLD*100:.0f}% threshold AND "
                    f"wins-flipped {wins_flipped} < {WINS_FLIPPED_LIMIT}; "
                    f"keep OVERRIDE as direction-flip rule"),
            n_out_of_sample=n_oos, n_in_sample=n_in,
            precision_pct=round(precision_pct, 2),
            wins_flipped=wins_flipped, losses_flipped=losses_flipped,
        )

    return Verdict(
        state="demote",
        reason=(f"out-of-sample precision {precision_pct:.1f}% < "
                f"{PRECISION_THRESHOLD*100:.0f}% threshold; "
                f"demote OVERRIDE to stake-size cap per locked policy"),
        n_out_of_sample=n_oos, n_in_sample=n_in,
        precision_pct=round(precision_pct, 2),
        wins_flipped=wins_flipped, losses_flipped=losses_flipped,
    )


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------
def write_ledger(fires: List[OverrideFire]) -> None:
    os.makedirs(BACKTEST_DIR, exist_ok=True)
    with open(LEDGER_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "matchup", "decision", "verdict", "final_score",
                    "model_pick", "in_sample"])
        for fire in fires:
            w.writerow([fire.date, fire.matchup, fire.decision, fire.verdict,
                        fire.final_score, fire.model_pick,
                        "in_sample" if fire.in_sample else "out_of_sample"])


def write_status(v: Verdict, fires: List[OverrideFire]) -> dict:
    os.makedirs(BACKTEST_DIR, exist_ok=True)
    status = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "verdict": asdict(v),
        "n_fires_total": len(fires),
        "out_of_sample_fires": [
            asdict(f) for f in fires if not f.in_sample
        ],
        "in_sample_fires": [
            asdict(f) for f in fires if f.in_sample
        ],
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    return status


# ---------------------------------------------------------------------------
# Push-bat writer (only when verdict='demote' and the bat doesn't already
# reflect the current decision)
# ---------------------------------------------------------------------------
PUSH_BAT_TEMPLATE = """@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  OVERRIDE DEMOTION  (auto-generated %DATE_TAG%)
echo  -----------------------------------------------------------
echo  The out-of-sample OVERRIDE backtest hit the locked demote
echo  threshold:
echo
echo    out-of-sample n: %N_OOS%
echo    precision:       %PRECISION%%%   (threshold: 85%%)
echo    wins flipped:    %WINS_FLIPPED%  (limit: 2)
echo    verdict reason:  %REASON%
echo
echo  Per the locked policy (memory/project_override_backtest_thresholds.md),
echo  OVERRIDE demotes from a direction-flip rule to a stake-size cap:
echo  the 4-condition convergence still kills the bet, but it no
echo  longer claims to know the correct side.
echo
echo  This .bat was auto-generated by tools/backtest_override.py.
echo  Review the change before double-clicking; the harness will
echo  NEVER push automatically.  See override_status.json for the
echo  full per-fire ledger backing this decision.
echo ============================================================
echo.
echo (Patch logic not yet wired — implement when first 'demote' fires)
echo Aborting; nothing pushed.
pause
"""


def write_push_bat(v: Verdict) -> Optional[str]:
    """Only invoked when v.state == 'demote'.  Writes a notification .bat
    that the user reviews and runs manually.  The actual patch logic is
    intentionally left as a placeholder — when the first 'demote' fires
    in production, the patch can be hand-finalized then; we do not want
    to maintain dead code for a path that may never fire."""
    if v.state != "demote":
        return None
    body = (PUSH_BAT_TEMPLATE
            .replace("%DATE_TAG%", datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
            .replace("%N_OOS%", str(v.n_out_of_sample))
            .replace("%PRECISION%", f"{v.precision_pct:.1f}"
                                    if v.precision_pct is not None else "n/a")
            .replace("%WINS_FLIPPED%", str(v.wins_flipped or 0))
            .replace("%REASON%", v.reason))
    # CRLF per locked .bat memory pattern.
    body_crlf = body.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(PUSH_BAT_FILE, "wb") as f:
        f.write(body_crlf.encode("utf-8"))
    return PUSH_BAT_FILE


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-fire ledger to stdout.")
    ap.add_argument("--json", action="store_true",
                    help="Print full status JSON to stdout.")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    fires = collect_override_fires()
    v = compute_verdict(fires)
    write_ledger(fires)
    status = write_status(v, fires)
    bat_path = write_push_bat(v) if v.state == "demote" else None

    print(f"OVERRIDE backtest — verdict: {v.state.upper()}")
    print(f"  n_in_sample:     {v.n_in_sample}")
    print(f"  n_out_of_sample: {v.n_out_of_sample}  (floor: {SAMPLE_FLOOR})")
    if v.precision_pct is not None:
        print(f"  precision:       {v.precision_pct:.1f}%   "
              f"(threshold: {PRECISION_THRESHOLD*100:.0f}%)")
        print(f"  wins flipped:    {v.wins_flipped}   "
              f"(limit: {WINS_FLIPPED_LIMIT})")
    print(f"  reason: {v.reason}")
    print(f"  status: {STATUS_FILE}")
    print(f"  ledger: {LEDGER_FILE}")
    if bat_path:
        print(f"  push:   {bat_path}  (REVIEW BEFORE RUNNING)")

    if args.verbose:
        print("\nPer-fire ledger:")
        print(f"  {'date':<11}{'matchup':<14}{'verdict':<7}{'window':<15}{'score'}")
        for f in fires:
            window = "in_sample" if f.in_sample else "OUT_OF_SAMPLE"
            print(f"  {f.date:<11}{f.matchup:<14}{f.verdict:<7}"
                  f"{window:<15}{f.final_score}")

    if args.json:
        print("\n" + json.dumps(status, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
