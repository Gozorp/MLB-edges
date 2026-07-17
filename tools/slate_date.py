#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/slate_date.py -- single source of truth for "today's slate date".

MLB slates are keyed to US Eastern Time. Before this helper the entry
points disagreed (predict.py used the PC-local date, the nightly chain
used the UTC date, the postgame tools UTC-yesterday...), so every evening
between 00:00 UTC and local midnight different jobs could target
different dates. slate_today() computes the current date in
America/New_York so every job and tool agrees.

Usable as a module:   from tools.slate_date import slate_today
or from a .bat/shell (run from the repo root):
    python -c "from tools.slate_date import slate_today; print(slate_today())"
"""
import datetime

try:
    # Verified 2026-07-17: ZoneInfo("America/New_York") resolves on this box
    # (tzdata is installed) for both C:\Python313 and the project venv.
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:      # pragma: no cover - tzdata missing on this interpreter
    _ET = None


def _et_offset_manual(now_utc):
    """Fallback ET offset when tzdata is unavailable: UTC-4 during US DST
    (second Sunday in March 2:00 -> first Sunday in November 2:00, local),
    else UTC-5. Evaluated on local standard time per the US rule."""
    year = now_utc.year

    def _nth_sunday(month, n):
        d = datetime.date(year, month, 1)
        d += datetime.timedelta(days=(6 - d.weekday()) % 7)  # first Sunday
        return d + datetime.timedelta(weeks=n - 1)

    # Transitions occur at 2:00 local standard time = 07:00 UTC.
    dst_start = datetime.datetime(year, 3, _nth_sunday(3, 2).day, 7,
                                  tzinfo=datetime.timezone.utc)
    dst_end = datetime.datetime(year, 11, _nth_sunday(11, 1).day, 7,
                                tzinfo=datetime.timezone.utc)
    return -4 if dst_start <= now_utc < dst_end else -5


def slate_today():
    """Current date in US Eastern Time (the MLB slate date) as datetime.date."""
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if _ET is not None:
        return now_utc.astimezone(_ET).date()
    return (now_utc + datetime.timedelta(hours=_et_offset_manual(now_utc))).date()


if __name__ == "__main__":
    print(slate_today().isoformat())
