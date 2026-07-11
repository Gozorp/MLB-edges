#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/feature_coverage_report.py — per-bake INPUT HEALTH sidecar (2026-07-10).
Flaw fix (visibility half): the model emits identically-confident numbers
whether its inputs were rich or degraded. This sidecar records, per slate date,
exactly how healthy the inputs were, so (a) the dashboard can show it, and
(b) July's pre-registered coverage-soft-cap decision has accumulated data.

Writes docs/data/feature_coverage_<date>.json:
  savant_ok/total, fangraphs_ok/total   (parsed from local_slate_run.log)
  bref_age_days                          (newest standings snapshot vs today)
  games / with_market / pending          (from the baked diag)
  anchor_spread                          (median/max Kalshi bid-ask, if cached)
  status: green|yellow|red               (display heuristic ONLY — changes
                                          nothing; the soft-cap rule is gated
                                          behind INPUT_INTEGRITY_PREREG.md)
Display/telemetry only. Never touches model inputs or outputs.
"""
from __future__ import annotations

import csv
import datetime
import glob
import json
import os
import re
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
csv.field_size_limit(10 ** 7)


def parse_log():
    """Endpoint counts print to whichever stream the bake ran under: direct
    console runs land in local_slate_run.log, chain runs land in
    logs/midnight.log. Check both; take the LAST occurrence (current bake)."""
    sav_ok = sav_tot = fg_ok = fg_tot = None
    for path in ("local_slate_run.log", os.path.join("logs", "midnight.log")):
        try:
            txt = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        ms = list(re.finditer(r"\(Savant\):\s*(\d+)/(\d+)\s*endpoints OK", txt))
        if ms:
            sav_ok, sav_tot = int(ms[-1].group(1)), int(ms[-1].group(2))
        ms = list(re.finditer(r"\(FanGraphs\):\s*(\d+)/(\d+)\s*endpoints OK", txt))
        if ms:
            fg_ok, fg_tot = int(ms[-1].group(1)), int(ms[-1].group(2))
        if sav_ok is not None:
            break
    return sav_ok, sav_tot, fg_ok, fg_tot


def bref_age():
    fs = sorted(glob.glob("data/bref/standings/*_upto-*overall.csv"))
    if not fs:
        return None
    m = re.search(r"(\d{8})_", os.path.basename(fs[-1]))
    if not m:
        return None
    d = datetime.datetime.strptime(m.group(1), "%Y%m%d").date()
    return (datetime.date.today() - d).days


def diag_stats(date):
    p = "docs/data/picks_%s_diag.csv" % date
    if not os.path.exists(p):
        return None
    rows = list(csv.DictReader(open(p, encoding="utf-8", errors="replace")))
    return {
        "games": len(rows),
        "with_market": sum(1 for r in rows if (r.get("fair_prob") or "").strip()),
        "pending": sum(1 for r in rows if "PENDING" in (r.get("tier") or "")),
    }


def anchor_spread(date):
    p = "data/news_cache/anchors/anchor_%s.json" % date
    if not os.path.exists(p):
        return None
    try:
        j = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    spreads = []

    def walk(o):
        if isinstance(o, dict):
            bid = o.get("yes_bid_dollars") or o.get("yes_bid")
            ask = o.get("yes_ask_dollars") or o.get("yes_ask")
            try:
                b, a = float(bid), float(ask)
                if 0 < b <= a <= 1.5:
                    spreads.append(round((a - b) * (100 if a <= 1 else 1), 2))
            except (TypeError, ValueError):
                pass
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(j)
    if not spreads:
        return None
    spreads.sort()
    return {"n": len(spreads), "median_pp": spreads[len(spreads) // 2],
            "max_pp": spreads[-1]}


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat()
    sav_ok, sav_tot, fg_ok, fg_tot = parse_log()
    age = bref_age()
    dstats = diag_stats(date) or {}
    rep = {
        "date": date,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                         .isoformat(timespec="seconds"),
        "savant_ok": sav_ok, "savant_total": sav_tot,
        "fangraphs_ok": fg_ok, "fangraphs_total": fg_tot,
        "bref_age_days": age,
        "anchor_spread": anchor_spread(date),
        **dstats,
    }
    bad = ((sav_ok is not None and sav_ok < 30)
           or (age is not None and age > 7)
           or (dstats.get("games") and dstats.get("with_market", 0) * 2 < dstats["games"]))
    warn = ((sav_ok is not None and sav_ok < sav_tot)
            or (fg_ok == 0) or (age is not None and age > 1)
            or (dstats.get("pending", 0) > 3))
    rep["status"] = "red" if bad else ("yellow" if warn else "green")

    out = "docs/data/feature_coverage_%s.json" % date
    tmp = out + ".tmp"
    json.dump(rep, open(tmp, "w", encoding="utf-8"), indent=1)
    os.replace(tmp, out)
    print("feature coverage [%s] -> %s  (savant %s/%s, FG %s/%s, bref %sd, "
          "market %s/%s, pending %s)"
          % (rep["status"], out, sav_ok, sav_tot, fg_ok, fg_tot, age,
             dstats.get("with_market"), dstats.get("games"), dstats.get("pending")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("feature_coverage FAILED: %r" % (e,))
        sys.exit(1)
