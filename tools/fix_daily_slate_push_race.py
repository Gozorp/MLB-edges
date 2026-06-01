#!/usr/bin/env python3
"""
fix_daily_slate_push_race.py
----------------------------
Stop the scheduled "Daily slate run" from failing on a git push race.

ROOT CAUSE (confirmed from run #207's log):
    [main 609f7b3] daily-slate: 2026-05-31 auto-run + bake
     ! [rejected]   main -> main (fetch first)
    error: failed to push some refs ... remote contains work you do not have
The commit step computes + commits the slate fine, then does a BARE `git push`.
main moves constantly (health-check every :07/:37, self-learn, refit, bake,
claude-brain), so when another workflow pushes inside this run's ~6-min window
the push is rejected non-fast-forward and the step exits 1 -> the whole run goes
red. It's intermittent (only on collision) and scheduled-only (hourly cadence
collides; one-off manual dispatches usually win the race).

FIX: replace the bare `git push` with a bounded rebase-and-retry loop (up to 5x,
`-X theirs` so our fresh bake wins any same-file overlap, small jitter). A
persistent failure emits ::warning:: and the next hourly run re-bakes, but it no
longer fails the run -- so transient races stop reddening the board. Same
resilience pattern the self-learn / health-check steps already use.

Single edit to .github/workflows/daily-slate.yml. Idempotent. Run from repo root.
"""
import sys

YML = ".github/workflows/daily-slate.yml"

OLD = (
    '            git commit -m "daily-slate: $(date -u +%Y-%m-%d) auto-run + bake"\n'
    "            git push\n"
    "          fi"
)

NEW = (
    '            git commit -m "daily-slate: $(date -u +%Y-%m-%d) auto-run + bake"\n'
    "            # main moves often (health-check :07/:37, self-learn, refit,\n"
    "            # bake, claude-brain). A bare push hits non-fast-forward\n"
    "            # rejections, which used to FAIL the scheduled run ~half the\n"
    "            # time. Rebase-and-retry up to 5x; -X theirs keeps our fresh\n"
    "            # bake on any same-file overlap. A persistent failure warns\n"
    "            # (next hourly run re-bakes) but does NOT fail the run, so\n"
    "            # transient races stop reddening the board.\n"
    "            for i in 1 2 3 4 5; do\n"
    '              if git push origin main; then echo "pushed (attempt $i)"; break; fi\n'
    '              echo "push rejected (attempt $i) - rebasing on origin/main and retrying..."\n'
    "              git fetch origin main || true\n"
    "              git rebase -X theirs origin/main || git rebase --abort 2>/dev/null || true\n"
    '              if [ "$i" = "5" ]; then echo "::warning::daily-slate push still rejected after 5 attempts; next run will re-bake"; fi\n'
    "              sleep $((RANDOM % 4 + 2))\n"
    "            done\n"
    "          fi"
)

MARKER = "rebasing on origin/main and retrying"


def main():
    with open(YML, "r", encoding="utf-8", newline="") as f:
        raw = f.read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    work = raw.replace("\r\n", "\n")

    if MARKER in work:
        print(f"  skip (already applied): {MARKER}")
        return

    n = work.count(OLD)
    if n != 1:
        print(f"  ERROR anchor count={n} (need 1) in {YML}")
        sys.exit(1)

    work = work.replace(OLD, NEW, 1)
    with open(YML, "w", encoding="utf-8", newline="") as f:
        f.write(work.replace("\n", nl))
    print(f"  applied push-race retry loop to {YML}")


if __name__ == "__main__":
    main()
