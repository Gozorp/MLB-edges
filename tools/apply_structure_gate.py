#!/usr/bin/env python3
"""
apply_structure_gate.py
-----------------------
Wire slate_structure_gate.py into the daily-slate commit step: skip the commit
entirely when no game's SP/lineup changed vs HEAD, so the per-bake Monte-Carlo /
odds jitter stops churning commits.

Inserts a gate call right before `git add` in the "Commit + push" step of
.github/workflows/daily-slate.yml. Idempotent. Run from the repo root.
"""
import sys

WF = ".github/workflows/daily-slate.yml"
MARK = "slate_structure_gate.py"

OLD = (
    '          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"\n'
    '          git add picks_*.csv parlay_*.txt docs/data/ data/state/ 2>/dev/null || true'
)
NEW = (
    '          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"\n'
    '          # Structure gate: skip the commit when SP/lineups are unchanged vs\n'
    '          # HEAD. The diag still churns every bake from Monte-Carlo/odds\n'
    '          # jitter; committing that is pure noise. data/state does not change\n'
    '          # in this job, so skipping loses nothing real. The gate fail-safes\n'
    '          # to commit on any error, so it can never freeze the slate.\n'
    '          if ! python tools/slate_structure_gate.py; then\n'
    '            echo "slate structure unchanged vs HEAD - skipping churn commit"\n'
    '            exit 0\n'
    '          fi\n'
    '          git add picks_*.csv parlay_*.txt docs/data/ data/state/ 2>/dev/null || true'
)


def main():
    with open(WF, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")
    if MARK in work:
        print("  skip (already applied)")
        return
    if work.count(OLD) != 1:
        print(f"  ERROR anchor count={work.count(OLD)} (need 1)")
        sys.exit(1)
    work = work.replace(OLD, NEW, 1)
    with open(WF, "w", encoding="utf-8", newline="") as f:
        f.write(work.replace("\n", nl))
    print("  applied: structure gate wired into daily-slate commit step")


if __name__ == "__main__":
    main()
