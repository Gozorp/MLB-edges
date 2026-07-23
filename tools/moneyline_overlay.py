#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
moneyline_overlay.py -- post-hoc moneyline market-blend overlay.
DISPLAY / DECISION AID ONLY.

Runs AFTER predict.py writes picks_<date>_diag.csv. Never touches the frozen
model; it appends blended columns to the day's diag (atomic rewrite). Mirrors
tools/totals_overlay.py, which does the same job for totals.

Why this exists: on graded history the raw model probability has no measurable
resolution and loses to a constant on Brier, while the de-vigged market has
real resolution. A convex blend p = w*model + (1-w)*market recovers the small
slice of information the model carries beyond the line -- tools/baseline_eval.py
measures the best w at ~0.25 (Brier 0.2444, beating both model-only and
market-only on the same games; matches the internal 2026-07-17 audit's 25/75).

Columns added (all pick-perspective, matching p_model / fair_prob):
  pick_prob_blend   w*p_model + (1-w)*fair_prob   (fair present)
                    = p_model                     (no line -> cannot blend)
  edge_pp_blend     (pick_prob_blend - fair_prob)*100  (fair present) else ""
                    NB: blending toward the market shrinks the edge to
                    w * raw_edge -- an honest statement that only ~w of the
                    apparent edge survives contact with the line.
  blend_basis       "blend" | "no_line" | ""  (p_model missing)

The blend weight is read from data/state/moneyline_blend.json ("model_weight",
refittable by re-running baseline_eval and updating that file); defaults to
0.25 if the file is absent. Fully sandboxed: any failure prints a warning and
leaves the CSV unchanged.

Usage: python tools/moneyline_overlay.py [YYYY-MM-DD]   (default: newest diag)
"""
import csv, glob, json, os, sys

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
STATE = os.path.join("data", "state", "moneyline_blend.json")
DEFAULT_WEIGHT = 0.25
NEW_COLS = ["pick_prob_blend", "edge_pp_blend", "blend_basis"]


def _target_csv():
    if len(sys.argv) > 1:
        return "picks_%s_diag.csv" % sys.argv[1]
    files = sorted(glob.glob("picks_????-??-??_diag.csv"))
    return files[-1] if files else None


def _num(v):
    try:
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none"):
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _weight():
    try:
        w = float(json.load(open(STATE, encoding="utf-8"))["model_weight"])
        if 0.0 <= w <= 1.0:
            return w
    except Exception:
        pass
    return DEFAULT_WEIGHT


def main():
    path = _target_csv()
    if not path or not os.path.exists(path):
        print("moneyline_overlay: no diag CSV found (%s) -- nothing to do" % path)
        return
    w = _weight()

    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    if not rows:
        print("moneyline_overlay: %s is empty -- nothing to do" % path)
        return
    fields = list(rows[0].keys()) + [c for c in NEW_COLS if c not in rows[0]]

    n_blend = n_noline = 0
    for r in rows:
        pm = _num(r.get("p_model"))
        if pm is None:
            pm = _num(r.get("pick_prob"))
        fair = _num(r.get("fair_prob"))
        if pm is None:
            r["pick_prob_blend"] = ""
            r["edge_pp_blend"] = ""
            r["blend_basis"] = ""
            continue
        if fair is not None:
            blend = w * pm + (1.0 - w) * fair
            r["pick_prob_blend"] = "%.4f" % blend
            r["edge_pp_blend"] = "%.2f" % ((blend - fair) * 100.0)
            r["blend_basis"] = "blend"
            n_blend += 1
        else:
            r["pick_prob_blend"] = "%.4f" % pm
            r["edge_pp_blend"] = ""
            r["blend_basis"] = "no_line"
            n_noline += 1

    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        wri = csv.DictWriter(f, fieldnames=fields)
        wri.writeheader()
        wri.writerows(rows)
    os.replace(tmp, path)
    print("moneyline_overlay: %s -- %d rows blended (w=%.2f: %d blend, %d no-line)"
          % (path, n_blend + n_noline, w, n_blend, n_noline))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("moneyline_overlay: WARN unexpected failure %r -- CSV left unchanged" % (e,))
