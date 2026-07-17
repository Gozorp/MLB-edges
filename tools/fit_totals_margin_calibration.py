#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fit_totals_margin_calibration.py -- fit the totals + margin display calibration
from historical performance (2026-07-17 analysis, user-directed).

Reads:  bt_totals_2023/2024/2025.csv           (pred_runs, total_line, actual_runs)
        picks_totals_2026-*.csv                (pred_runs, total_line)
        picks_2026-*_diag.csv                  (pick, pick_prob / p_model)
        docs/data/_results_*.json + docs/data/postgame/*.json   (ground truth)
Writes: data/state/totals_margin_calibration.json  (atomic)

Findings the parameters encode (walk-forward validated, n=2,191 totals games,
488 margin games; see TOTALS_MARGIN_RECAL_2026-07-17.md):
  * pred_runs alone has ~zero correlation with actual totals (r = 0.03-0.11 by
    season); a market-line blend cuts OOS MAE 3.86 -> 3.23 and P10-P90 interval
    width 12.4 -> 10.5 runs, with bias +0.07 (raw: -0.42 in 2024).
  * pick_prob -> margin is flat-to-inverted OOS, so the spread curve is
    clamped to a gentle monotone band around the MAE-optimal flat "+1".
Rerun any time to refit: python tools/fit_totals_margin_calibration.py
"""
import csv, glob, json, os, re, sys, datetime
import numpy as np

ROOT = os.environ.get("MLB_EDGE_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
OUT = os.path.join("data", "state", "totals_margin_calibration.json")
S = lambda x: (x if isinstance(x, str) else "") if x is not None else ""
QS = (5, 10, 25, 50, 75, 90, 95)


def _truth():
    """(date, away, home) -> (away_runs, home_runs); _results wins over postgame."""
    truth = {}
    for f in glob.glob("docs/data/postgame/*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for m, i in (d.get("by_matchup", {}) or {}).items():
            if not isinstance(i, dict): continue
            if "(G" in S(m): continue   # DH game-2 keys: first-game convention here
            sc = S(i.get("final_score")).strip()
            mm = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(m))
            if mm and re.match(r"^\d+-\d+$", sc):
                a, b = map(int, sc.split("-"))  # away-first (verified vs _results)
                truth[(date, mm.group(1), mm.group(2))] = (a, b)
    for f in glob.glob("docs/data/_results_*.json"):
        try: d = json.load(open(f, encoding="utf-8"))
        except Exception: continue
        date = S(d.get("date"))
        for g in d.get("games", []):
            if g.get("status") != "Final": continue
            try:
                truth[(date, S(g["away"]).strip(), S(g["home"]).strip())] = \
                    (int(g["away_runs"]), int(g["home_runs"]))
            except Exception: pass
    return truth


def _totals_rows(truth):
    """[(year, pred, line_or_nan, actual)] from backtests + joined 2026 slates."""
    rows = []
    for yr in (2023, 2024, 2025):
        fn = "bt_totals_%d.csv" % yr
        if not os.path.exists(fn): continue
        for r in csv.DictReader(open(fn, encoding="utf-8")):
            try:
                rows.append((yr, float(r["pred_runs"]),
                             float(r["total_line"]) if S(r.get("total_line")) else np.nan,
                             float(r["actual_runs"])))
            except Exception: pass
    seen = set()
    for f in sorted(glob.glob("picks_totals_2026-*.csv")):
        for r in csv.DictReader(open(f, encoding="utf-8")):
            key = (S(r.get("game_date")), S(r.get("away_team")).strip(), S(r.get("home_team")).strip())
            if not key[0] or key in seen: continue   # DH dupes: first game only
            seen.add(key)
            if key not in truth: continue
            try:
                rows.append((2026, float(r["pred_runs"]),
                             float(r["total_line"]) if S(r.get("total_line")) else np.nan,
                             float(sum(truth[key]))))
            except Exception: pass
    return rows


def _fit_totals(rows):
    A = np.array(rows, dtype=float)          # year, pred, line, actual
    yr, p, l, a = A[:, 0], A[:, 1], A[:, 2], A[:, 3]
    hl = ~np.isnan(l)

    def blend_fit(m):
        X = np.column_stack([p[m], l[m], np.ones(int(m.sum()))])
        return np.linalg.lstsq(X, a[m], rcond=None)[0]

    # walk-forward OOS residuals (train prior years -> test year) for honest bands
    oos_res, oos_raw = [], []
    for ty in (2024, 2025, 2026):
        tr = hl & (yr < ty); te = hl & (yr == ty)
        if tr.sum() < 50 or te.sum() < 10: continue
        c = blend_fit(tr)
        oos_res.append(a[te] - (c[0] * p[te] + c[1] * l[te] + c[2]))
        oos_raw.append(a[te] - p[te])
    res = np.concatenate(oos_res); raw = np.concatenate(oos_raw)

    coef = blend_fit(hl)                                  # deploy: fit on all
    lin = np.polyfit(p, a, 1)                             # no-line fallback
    lin_res = a - np.polyval(lin, p)

    # discrete offsets of actual around the rounded calibrated total (OOS)
    offs = np.round(res).astype(int)
    vals, cnts = np.unique(offs, return_counts=True)
    top = sorted(zip(vals.tolist(), (cnts / len(offs)).tolist()), key=lambda t: -t[1])[:5]

    return {
        "blend": {"coef_pred": round(float(coef[0]), 4), "coef_line": round(float(coef[1]), 4),
                  "intercept": round(float(coef[2]), 4), "n_fit": int(hl.sum())},
        "linear_no_line": {"slope": round(float(lin[0]), 4), "intercept": round(float(lin[1]), 4),
                           "n_fit": int(len(p))},
        "resid_q_oos": {str(q): round(float(np.percentile(res, q)), 2) for q in QS},
        "resid_q_no_line": {str(q): round(float(np.percentile(lin_res, q)), 2) for q in QS},
        "offset_probs_top5": [[int(v), round(pr, 4)] for v, pr in top],
        "oos_metrics": {"n": int(len(res)),
                        "mae_raw": round(float(np.abs(raw).mean()), 3),
                        "mae_cal": round(float(np.abs(res).mean()), 3),
                        "bias_raw": round(float(raw.mean()), 3),
                        "bias_cal": round(float(res.mean()), 3),
                        "p10_p90_width_raw": round(float(np.percentile(raw, 90) - np.percentile(raw, 10)), 2),
                        "p10_p90_width_cal": round(float(np.percentile(res, 90) - np.percentile(res, 10)), 2)},
    }


def _margin_rows(truth):
    rows = []
    for f in sorted(glob.glob("picks_2026-*_diag.csv")):
        m = re.search(r"picks_(2026-\d\d-\d\d)_diag", f)
        if not m: continue
        date = m.group(1); seen = set()
        try: rdr = list(csv.DictReader(open(f, encoding="utf-8")))
        except Exception: continue
        for r in rdr:
            mu = re.match(r"\s*([A-Za-z0-9]+)\s*@\s*([A-Za-z0-9]+)", S(r.get("matchup")))
            if not mu: continue
            key = (date, mu.group(1), mu.group(2))
            if key in seen or key not in truth: continue
            seen.add(key)
            p = S(r.get("pick_prob")) or S(r.get("p_model")); pick = S(r.get("pick")).strip()
            if not p or not pick: continue
            try: p = float(p)
            except Exception: continue
            ar, hr = truth[key]
            if ar == hr: continue
            if pick == mu.group(2):   fm = hr - ar
            elif pick == mu.group(1): fm = ar - hr
            else: continue
            rows.append((max(p, 1.0 - p), fm))
    return rows


def _fit_margin(rows):
    M = np.array(rows, dtype=float)
    p, am = M[:, 0], M[:, 1]
    dist = {}
    for lo, hi in ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 1.01)):
        msk = (p >= lo) & (p < hi)
        if msk.sum() < 15: continue
        vals, cnts = np.unique(am[msk], return_counts=True)
        top = sorted(zip(vals.tolist(), (cnts / msk.sum()).tolist()), key=lambda t: -t[1])[:5]
        dist["%.2f-%.2f" % (lo, hi)] = {"n": int(msk.sum()),
                                        "p_win": round(float((am[msk] > 0).mean()), 3),
                                        "mean_margin": round(float(am[msk].mean()), 2),
                                        "top5": [[int(v), round(pr, 3)] for v, pr in top]}
    vals, cnts = np.unique(am, return_counts=True)
    top_all = sorted(zip(vals.tolist(), (cnts / len(am)).tolist()), key=lambda t: -t[1])[:5]
    return {
        # gentle monotone curve clamped near the MAE-optimal flat "+1"
        # (empirical logit slope is ~0/negative OOS -- large spreads are never earned)
        "curve": {"intercept": 0.85, "logit_coef": 0.45, "clip_lo": 0.5, "clip_hi": 2.5},
        "scale_range": [0.9, 1.1],
        "most_probable_margin": {"value": 1, "prob": round(float((am == 1).mean()), 3),
                                 "note": "favored team by exactly 1 run"},
        "margin_top5_overall": [[int(v), round(pr, 3)] for v, pr in top_all],
        "dist_by_pickprob": dist,
        "metrics": {"n": int(len(am)), "favored_win_pct": round(float((am > 0).mean()), 3),
                    "median_margin": float(np.median(am)),
                    "mae_flat_plus1": round(float(np.abs(am - 1).mean()), 3)},
    }


def main():
    truth = _truth()
    trows = _totals_rows(truth)
    mrows = _margin_rows(truth)
    if len(trows) < 500 or len(mrows) < 100:
        print("FATAL: thin sample (totals=%d margin=%d) -- refusing to fit" % (len(trows), len(mrows)))
        sys.exit(1)
    out = {
        "fitted_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "totals": _fit_totals(trows),
        "margin": _fit_margin(mrows),
        "spec": "TOTALS_MARGIN_RECAL_2026-07-17.md",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, OUT)
    t = out["totals"]["oos_metrics"]; m = out["margin"]["metrics"]
    print("wrote %s" % OUT)
    print("totals: n_fit=%d  OOS n=%d  MAE %.3f->%.3f  bias %+.3f->%+.3f  P10-P90 %.1f->%.1f"
          % (out["totals"]["blend"]["n_fit"], t["n"], t["mae_raw"], t["mae_cal"],
             t["bias_raw"], t["bias_cal"], t["p10_p90_width_raw"], t["p10_p90_width_cal"]))
    print("margin: n=%d  favored win %.1f%%  median margin %+.0f  P(exact +1)=%.3f"
          % (m["n"], 100 * m["favored_win_pct"], m["median_margin"],
             out["margin"]["most_probable_margin"]["prob"]))


if __name__ == "__main__":
    main()
