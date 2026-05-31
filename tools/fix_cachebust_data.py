#!/usr/bin/env python3
"""
fix_cachebust_data.py
---------------------
Stop the dashboard's own data files (diag CSV, parlay txt) from being served
stale out of the browser cache after a deploy/bake.

Root cause: loadSlate(date, {bust=false}) only cache-busts when bust=true, but
the INITIAL page load calls it with bust=false -> q="" and fetchOpts={} (default
browser cache). So the first diag fetch could come from a stale cache (this is
why a freshly-baked feature like HR-prop showed the "not baked" fallback until a
hard-refresh). The auto-refresh path (silentRefresh) and the other sidecars
already use no-store; only this initial load did not.

Fix: always cache-bust in loadSlate. The `bust` flag now only changes the status
text. One JS edit, CSS/HTML untouched.

Idempotent. Run from the repo root.
"""
import sys

IDX = "docs/index.html"

OLD = (
    '    const q = bust ? `?v=${Date.now()}` : "";\n'
    '    const fetchOpts = bust ? { cache: "no-store" } : {};'
)
NEW = (
    '    // Always cache-bust the data fetches so a fresh deploy/bake is never\n'
    '    // masked by a stale browser cache. (`bust` now only affects status text.)\n'
    '    const q = `?v=${Date.now()}`;\n'
    '    const fetchOpts = { cache: "no-store" };'
)
MARK = "Always cache-bust the data fetches"


def main():
    with open(IDX, "r", encoding="utf-8", newline="") as f:
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
    with open(IDX, "w", encoding="utf-8", newline="") as f:
        f.write(work.replace("\n", nl))
    print("  applied: loadSlate always cache-busts data fetches")


if __name__ == "__main__":
    main()
