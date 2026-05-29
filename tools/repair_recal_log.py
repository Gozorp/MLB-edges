#!/usr/bin/env python3
"""Repair data/state/recalibration_log.jsonl.

Drops any line that is not valid JSON -- e.g. a NUL-padded partial write left
behind when an auto_weight_update run is interrupted mid-append (observed
2026-05-28: the 05-27 entry was 918 NUL bytes). Backs the original up to
<file>.corrupt_backup before rewriting. Idempotent: if every non-blank line
already parses, it does nothing. Safe to run anytime.
"""
import json
import shutil
import sys
from pathlib import Path

LOG = Path("data/state/recalibration_log.jsonl")


def main() -> int:
    if not LOG.exists():
        print(f"{LOG} does not exist; nothing to repair")
        return 0
    raw = LOG.read_bytes()
    good = []
    bad = 0
    for line in raw.split(b"\n"):
        s = line.strip()
        if not s:
            continue
        try:
            json.loads(s.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            bad += 1
            continue
        good.append(s)
    if bad == 0:
        print(f"no corruption: all {len(good)} lines valid; left as-is")
        return 0
    backup = LOG.with_suffix(LOG.suffix + ".corrupt_backup")
    shutil.copy2(LOG, backup)
    LOG.write_bytes(b"\n".join(good) + b"\n")
    print(f"repaired: dropped {bad} corrupt line(s), kept {len(good)} valid; "
          f"backup -> {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
