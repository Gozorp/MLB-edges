# -*- coding: utf-8 -*-
"""
tools/calibration_backtest.py — the CALIBRATION_SPEC.md bake-off harness.
============================================================================
READ-ONLY pre-build (2026-07-09), sanctioned by the spec's §11 checklist and
its prototyping clause. This harness fits NOTHING to production and writes
NOTHING to data/state/ or docs/data/ — stdout only. The PRODUCTION decision
(fit on the frozen-era ledger pool, apply the §7 acceptance bar) runs only
after the freeze lifts AND the pool clears the §3 min-n gate (>= 350 frozen
graded picks). Until then: `--selftest` proves the harness logic on synthetic
data; `--ledger` reports pool/gate status and dry-runs the walk-forward
descriptively (clearly labeled NOT-A-DECISION below min-n).

Spec bindings (CALIBRATION_SPEC.md, locked 2026-06-09):
  §3 pool      : oos_ledger.jsonl, phase=result, slate_date >= 2026-06-04,
                 deduped per game; min-n 350 before any production fit.
  §5 bake-off  : C0 RAW / C1 binned-isotonic (n_bins x prior_alpha grid) /
                 C2 = C1 + tail clamp / C3 Platt (known-loser sanity ref).
  §6 partition : expanding walk-forward GROUPED BY game-day, burn-in 200
                 graded picks before the first OOS prediction.
  §7 gates     : PASS iff (a) dBrier>0 w/ 95% day-block-bootstrap CI lower
                 bound > 0, (b) ECE_cal <= ECE_raw, (c) logloss_cal <=
                 logloss_raw + 0.002, (d) n_OOS >= 250.
  §8 bootstrap : resample unit = game-DAY, B=10000, fixed seed, percentile CI.

Usage:
  python tools/calibration_backtest.py --selftest
  python tools/calibration_backtest.py --ledger            # status + dry-run
  python tools/calibration_backtest.py --ledger --decision # July: gates ON
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import OrderedDict

import numpy as np

LEDGER = os.path.join("docs", "data", "oos_ledger.jsonl")
FROZEN_START = "2026-06-04"
MIN_POOL_N = 350          # §3
BURN_IN = 200             # §6
MIN_OOS_N = 250           # §7(d)
LOGLOSS_TOL = 0.002       # §7(c)
B_BOOT = 10000            # §8
SEED = 7                  # §8 fixed RNG
EPS = 1e-12


# ---------------------------------------------------------------- candidates
def pava(rates, weights):
    """Pool-adjacent-violators: weighted isotonic (non-decreasing) fit."""
    r = [float(x) for x in rates]
    w = [float(x) for x in weights]
    blocks = [[r[i], w[i], i, i] for i in range(len(r))]
    out = []
    for b in blocks:
        out.append(b)
        while len(out) > 1 and out[-2][0] > out[-1][0] + 1e-15:
            v2, w2, s2, e2 = out.pop()
            v1, w1, s1, e1 = out.pop()
            wt = w1 + w2
            out.append([(v1 * w1 + v2 * w2) / max(wt, EPS), wt, s1, e2])
    fitted = [0.0] * len(r)
    for v, _, s, e in out:
        for i in range(s, e + 1):
            fitted[i] = v
    return fitted


class BinnedIsotonic:
    """§5 C1: equal-width bins, Beta(prior_alpha) shrink toward bin midpoint,
    weighted PAVA pass, linear interpolation between bin mids at predict."""

    def __init__(self, n_bins=10, prior_alpha=20, clamp=None):
        self.n_bins = n_bins
        self.prior_alpha = prior_alpha
        self.clamp = clamp
        self.mids, self.rates = None, None

    def fit(self, p, y):
        p = np.asarray(p, float)
        y = np.asarray(y, float)
        edges = np.linspace(0.0, 1.0, self.n_bins + 1)
        mids, rates, ws = [], [], []
        for i in range(self.n_bins):
            lo, hi = edges[i], edges[i + 1]
            mask = (p >= lo) & (p < hi) if i < self.n_bins - 1 else (p >= lo) & (p <= hi)
            n = int(mask.sum())
            mid = (lo + hi) / 2.0
            k = float(y[mask].sum()) if n else 0.0
            shrunk = (k + self.prior_alpha * mid) / (n + self.prior_alpha)
            mids.append(mid)
            rates.append(shrunk)
            ws.append(n + self.prior_alpha)
        self.mids = np.array(mids)
        self.rates = np.array(pava(rates, ws))
        return self

    def predict(self, p):
        p = np.asarray(p, float)
        out = np.interp(p, self.mids, self.rates)
        if self.clamp:
            out = np.clip(out, self.clamp[0], self.clamp[1])
        return out


class Platt:
    """§5 C3 sanity reference: logistic on logit(p) via Newton-IRLS."""

    def __init__(self, iters=25):
        self.a, self.b = 1.0, 0.0
        self.iters = iters

    @staticmethod
    def _logit(p):
        p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
        return np.log(p / (1 - p))

    def fit(self, p, y):
        x = self._logit(p)
        y = np.asarray(y, float)
        a, b = 1.0, 0.0
        for _ in range(self.iters):
            z = np.clip(a * x + b, -30, 30)
            mu = 1.0 / (1.0 + np.exp(-z))
            w = np.maximum(mu * (1 - mu), 1e-9)
            g0 = np.sum(mu - y)
            g1 = np.sum((mu - y) * x)
            h00 = np.sum(w)
            h01 = np.sum(w * x)
            h11 = np.sum(w * x * x)
            det = h00 * h11 - h01 * h01
            if abs(det) < 1e-12:
                break
            db = (h11 * g0 - h01 * g1) / det
            da = (-h01 * g0 + h00 * g1) / det
            a -= da
            b -= db
        self.a, self.b = float(a), float(b)
        return self

    def predict(self, p):
        z = np.clip(self.a * self._logit(p) + self.b, -30, 30)
        return 1.0 / (1.0 + np.exp(-z))


def candidates():
    """§5 grid. OrderedDict name -> factory."""
    out = OrderedDict()
    out["C0_raw"] = None
    for nb in (8, 10, 12):
        for pa in (10, 20, 40):
            out["C1_iso_b%d_a%d" % (nb, pa)] = (lambda nb=nb, pa=pa:
                                                BinnedIsotonic(nb, pa))
    for cap in (0.65, 0.68, 0.70):
        out["C2_iso_b10_a20_cap%02d" % int(cap * 100)] = (
            lambda cap=cap: BinnedIsotonic(10, 20, clamp=(0.05, cap)))
    out["C3_platt"] = lambda: Platt()
    return out


# ------------------------------------------------------------------- metrics
def brier(p, y):
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def logloss(p, y):
    p = np.clip(np.asarray(p, float), 1e-9, 1 - 1e-9)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p, y, bins=10):
    """Equal-mass 10-bin expected calibration error (§7b)."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    order = np.argsort(p)
    chunks = np.array_split(order, bins)
    tot = 0.0
    for c in chunks:
        if len(c) == 0:
            continue
        tot += (len(c) / len(p)) * abs(p[c].mean() - y[c].mean())
    return float(tot)


# ------------------------------------------------------------- walk-forward
def walk_forward(days, factory):
    """§6: expanding window grouped by day. days = ordered list of
    (day, p_array, y_array). Returns paired (raw, cal, y, day_idx) arrays."""
    raw_out, cal_out, y_out, day_out = [], [], [], []
    seen_p, seen_y = [], []
    n_seen = 0
    for di, (day, p, y) in enumerate(days):
        if n_seen >= BURN_IN and factory is not None:
            model = factory().fit(np.concatenate(seen_p), np.concatenate(seen_y))
            cal = model.predict(p)
        elif n_seen >= BURN_IN:
            cal = np.asarray(p, float)
        else:
            cal = None
        if cal is not None:
            raw_out.append(np.asarray(p, float))
            cal_out.append(np.asarray(cal, float))
            y_out.append(np.asarray(y, float))
            day_out.append(np.full(len(p), di))
        seen_p.append(np.asarray(p, float))
        seen_y.append(np.asarray(y, float))
        n_seen += len(p)
    if not raw_out:
        return None
    return (np.concatenate(raw_out), np.concatenate(cal_out),
            np.concatenate(y_out), np.concatenate(day_out))


def block_bootstrap_dbrier(raw, cal, y, day_idx, B=B_BOOT, seed=SEED):
    """§8: day-level block bootstrap of dBrier = Brier_raw - Brier_cal."""
    rng = np.random.default_rng(seed)
    days = np.unique(day_idx)
    per_day = {d: (raw[day_idx == d], cal[day_idx == d], y[day_idx == d])
               for d in days}
    deltas = np.empty(B)
    for b in range(B):
        pick = rng.choice(days, size=len(days), replace=True)
        r = np.concatenate([per_day[d][0] for d in pick])
        c = np.concatenate([per_day[d][1] for d in pick])
        yy = np.concatenate([per_day[d][2] for d in pick])
        deltas[b] = brier(r, yy) - brier(c, yy)
    return (float(np.percentile(deltas, 2.5)),
            float(np.median(deltas)),
            float(np.percentile(deltas, 97.5)))


def evaluate(days, decision=False):
    """Run the full bake-off over day-grouped data. Returns report rows."""
    rows = []
    for name, factory in candidates().items():
        wf = walk_forward(days, factory)
        if wf is None:
            return None
        raw, cal, y, di = wf
        b_raw, b_cal = brier(raw, y), brier(cal, y)
        row = {
            "candidate": name,
            "n_oos": int(len(y)),
            "brier_raw": round(b_raw, 5),
            "brier_cal": round(b_cal, 5),
            "dbrier": round(b_raw - b_cal, 5),
            "logloss_raw": round(logloss(raw, y), 5),
            "logloss_cal": round(logloss(cal, y), 5),
            "ece_raw": round(ece(raw, y), 5),
            "ece_cal": round(ece(cal, y), 5),
        }
        if decision and name != "C0_raw":
            lo, med, hi = block_bootstrap_dbrier(raw, cal, y, di)
            row["dbrier_ci"] = (round(lo, 5), round(med, 5), round(hi, 5))
            row["PASS"] = bool(
                lo > 0
                and row["ece_cal"] <= row["ece_raw"]
                and row["logloss_cal"] <= row["logloss_raw"] + LOGLOSS_TOL
                and row["n_oos"] >= MIN_OOS_N
            )
        rows.append(row)
    return rows


# ---------------------------------------------------------------- data pools
def load_ledger_days():
    if not os.path.exists(LEDGER):
        return [], 0
    by_day = {}
    seen = set()
    with open(LEDGER, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("phase") != "result":
                continue
            d = r.get("slate_date") or ""
            if d < FROZEN_START:
                continue
            key = (d, r.get("matchup"))
            if key in seen:
                continue
            seen.add(key)
            p, y = r.get("pick_prob"), r.get("outcome")
            if p is None or y is None:
                continue
            by_day.setdefault(d, ([], []))
            by_day[d][0].append(float(p))
            by_day[d][1].append(int(y))
    days = [(d, np.array(ps), np.array(ys))
            for d, (ps, ys) in sorted(by_day.items())]
    return days, len(seen)


def synth_days(n_days=60, per_day=12, miscal="over", seed=11):
    """Synthetic pool. miscal='over': y drawn from a strongly shrunk version
    of p (raw badly overconfident -> isotonic should win; wide p-spread so the
    recoverable Brier ~0.005 clears estimation noise). 'none': y drawn from p
    exactly (raw is truth -> nothing should beat it)."""
    rng = np.random.default_rng(seed)
    if miscal == "over":
        n_days = max(n_days, 120)
    days = []
    for d in range(n_days):
        if miscal == "over":
            p = np.clip(0.5 + rng.normal(0.0, 0.14, per_day), 0.12, 0.88)
            true = 0.5 + (p - 0.5) * 0.5        # heavy shrink toward coin flip
        else:
            p = np.clip(rng.beta(5, 4, per_day) * 0.5 + 0.3, 0.3, 0.85)
            true = p
        y = (rng.random(per_day) < true).astype(int)
        days.append(("day%03d" % d, p, y))
    return days


# --------------------------------------------------------------------- main
def show(rows, title):
    print("\n=== %s ===" % title)
    hdr = ("candidate", "n_oos", "brier_raw", "brier_cal", "dbrier",
           "ece_raw", "ece_cal", "logloss_cal")
    print("  " + " | ".join("%-20s" % h if h == "candidate" else "%9s" % h
                            for h in hdr))
    for r in rows:
        line = ("  %-22s" % r["candidate"]
                + " | ".join("%9s" % r[k] for k in hdr[1:]))
        if "PASS" in r:
            line += "   %s CI=%s" % ("PASS" if r["PASS"] else "null",
                                     r.get("dbrier_ci"))
        print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ledger", action="store_true")
    ap.add_argument("--decision", action="store_true",
                    help="July only: enforce §3 min-n and run §7/§8 gates")
    args = ap.parse_args()

    if args.selftest:
        print("SELFTEST 1: raw is truth -> nothing should meaningfully beat it")
        rows = evaluate(synth_days(miscal="none"))
        show(rows, "well-calibrated synthetic (expect dbrier ~<= 0)")
        best = max(r["dbrier"] for r in rows if r["candidate"] != "C0_raw")
        assert best < 0.004, "a candidate 'beat' truth by %.4f - harness leak?" % best

        print("\nSELFTEST 2: raw overconfident -> isotonic should win, "
              "and the C1 family should beat C3 Platt on dbrier")
        rows = evaluate(synth_days(miscal="over"))
        show(rows, "overconfident synthetic (expect C1 dbrier > 0)")
        c1 = max(r["dbrier"] for r in rows if r["candidate"].startswith("C1"))
        n_pos = sum(1 for r in rows
                    if r["candidate"].startswith("C1") and r["dbrier"] > 0)
        assert c1 > 0.0008 and n_pos >= 5, (
            "isotonic failed to recover known heavy miscalibration "
            "(best C1 dbrier %.5f, %d/9 positive)" % (c1, n_pos))
        print("\nselftest OK: harness detects heavy miscalibration and does "
              "not invent improvements over truth. (Note: selftest also "
              "demonstrates the spec's thesis — WEAK miscalibration on a "
              "narrow p-spread is NOT recoverable; estimation noise eats it.)")
        return

    if args.ledger:
        days, n = load_ledger_days()
        print("frozen-era pool: %d graded picks across %d game-days "
              "(gate: %d; burn-in: %d)" % (n, len(days), MIN_POOL_N, BURN_IN))
        if args.decision:
            if n < MIN_POOL_N:
                print("REFUSING to run decision gates: pool %d < %d (§3). "
                      "Wait for the gate." % (n, MIN_POOL_N))
                sys.exit(2)
            rows = evaluate(days, decision=True)
            show(rows, "PRODUCTION BAKE-OFF (§7 gates enforced)")
        else:
            if n <= BURN_IN:
                print("pool <= burn-in (%d): no OOS predictions possible yet — "
                      "status only." % BURN_IN)
                return
            rows = evaluate(days)
            show(rows, "DRY-RUN (descriptive only — NOT a decision; "
                       "pool below §3 gate)" if n < MIN_POOL_N
                 else "DRY-RUN (descriptive only)")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
