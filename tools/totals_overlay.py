#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
totals_overlay.py -- post-hoc totals calibration overlay. DISPLAY/DECISION AID ONLY.

Runs AFTER mlb_edge.main_totals writes picks_totals_<date>.csv. Never touches
the frozen model; it appends calibrated columns to the day's CSV (atomic
rewrite) using data/state/totals_margin_calibration.json (fit by
tools/fit_totals_margin_calibration.py; see TOTALS_MARGIN_RECAL_2026-07-17.md).

Columns added:
  pred_runs_cal        market-blend calibrated total (line present)
                       or shrunk linear fallback (no line)
  total_p25 total_p75  central 50% band for the final combined score
  total_p10 total_p90  outer 80% band
  most_probable_total  argmax discrete total (rounded cal + modal OOS offset)
  mpt_prob             empirical probability of that exact total
  cal_basis            "blend" | "no_line" | "" (calibration unavailable)

Usage: python tools/totals_overlay.py [YYYY-MM-DD]   (default: newest CSV)
Fully sandboxed: any failure prints a warning and leaves the CSV unchanged.
"""
import csv, glob, json, os, sys

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
CAL = os.path.join("data", "state", "totals_margin_calibration.json")
NEW_COLS = ["pred_runs_cal", "total_p25", "total_p75", "total_p10", "total_p90",
            "most_probable_total", "mpt_prob", "cal_basis"]


def _target_csv():
    if len(sys.argv) > 1:
        return "picks_totals_%s.csv" % sys.argv[1]
    files = sorted(glob.glob("picks_totals_????-??-??.csv"))
    return files[-1] if files else None


def main():
    path = _target_csv()
    if not path or not os.path.exists(path):
        print("totals_overlay: no picks_totals CSV found (%s) -- nothing to do" % path)
        return
    try:
        cal = json.load(open(CAL, encoding="utf-8"))["totals"]
    except Exception as e:
        print("totals_overlay: WARN cannot read %s (%r) -- leaving %s unchanged" % (CAL, e, path))
        return

    blend, lin = cal["blend"], cal["linear_no_line"]
    q, qn = cal["resid_q_oos"], cal["resid_q_no_line"]
    off_top = cal.get("offset_probs_top5") or [[0, 0.10]]

    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        print("totals_overlay: %s is empty -- nothing to do" % path)
        return
    fields = list(rows[0].keys()) + [c for c in NEW_COLS if c not in rows[0]]

    n_blend = n_noline = 0
    for r in rows:
        try:
            pred = float(r.get("pred_runs") or "")
        except ValueError:
            pred = None
        line = None
        try:
            line = float(r.get("total_line") or "")
        except ValueError:
            pass
        if pred is None:
            for c in NEW_COLS: r[c] = ""
            continue
        if line is not None:
            c0 = blend["coef_pred"] * pred + blend["coef_line"] * line + blend["intercept"]
            qq, basis = q, "blend"; n_blend += 1
        else:
            c0 = lin["slope"] * pred + lin["intercept"]
            qq, basis = qn, "no_line"; n_noline += 1
        mpt = int(round(c0)) + int(off_top[0][0])
        r.update({
            "pred_runs_cal": "%.2f" % c0,
            "total_p25": "%.1f" % (c0 + float(qq["25"])),
            "total_p75": "%.1f" % (c0 + float(qq["75"])),
            "total_p10": "%.1f" % (c0 + float(qq["10"])),
            "total_p90": "%.1f" % (c0 + float(qq["90"])),
            "most_probable_total": str(max(mpt, 0)),
            "mpt_prob": "%.3f" % float(off_top[0][1]),
            "cal_basis": basis,
        })

    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    print("totals_overlay: %s -- %d rows calibrated (%d blend, %d no-line)"
          % (path, n_blend + n_noline, n_blend, n_noline))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("totals_overlay: WARN unexpected failure %r -- CSV left unchanged" % (e,))
