#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/baseline_eval.py
----------------------
Honest out-of-sample scoreboard for the win model.

Both external reviews and the internal 2026-07-17 audit converged on the same
P0: *before* touching features, trees, or tiers, prove whether the published
probabilities beat trivial baselines on untouched forward data. This tool is
that proof. It never trains anything and never touches a repo file except an
optional JSON summary under data/state/.

For every graded pick it assembles:
    - p_model      : the model's PICK-perspective probability (diag `p_model`)
    - full_prob    : the model's HOME-perspective probability (diag `full_prob`)
    - fair_prob    : the de-vigged market probability, PICK-perspective
    - pick_won / home_won

and scores these predictors, in BOTH the home framing and the pick framing:

    model            the model
    constant         predict the sample base rate for every game
    always_home      predict P=1 for home (home framing only)
    market           the de-vigged market probability
    blend_w*         w * model + (1-w) * market, swept over w

Metrics per predictor: N, mean prediction, base rate, accuracy, Brier,
log-loss, AUC, and the Murphy decomposition Brier = reliability - resolution
+ uncertainty (resolution is the number that says whether the predictor
separates strong from weak games at all). Plus a permutation test on the
model's resolution: if the observed resolution is inside the shuffled-label
null band, the model has no measurable discriminating power.

Outcomes come from docs/data/_results_*.json (local, no network) by default;
pass --fetch to pull finals from statsapi for dates missing a local file.

Usage:
    python tools/baseline_eval.py                     # all baked slates
    python tools/baseline_eval.py --since 2026-05-01  # from a date
    python tools/baseline_eval.py --staked-only       # only would-be bets
    python tools/baseline_eval.py --json out.json     # also write a summary
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---- team-abbreviation normalization (mirror refit_post_calibrator) ----------
ABBR_FIX = {"CHW": "CWS", "WSH": "WSH", "OAK": "ATH",
            "KCR": "KC", "TBR": "TB", "ARI": "AZ"}


def _norm(t: str) -> str:
    t = (t or "").strip().upper()
    return ABBR_FIX.get(t, t)


def _num(row: dict, key: str) -> Optional[float]:
    """Parse a cell as float; None for missing/blank/nan. A legit 0.0 survives."""
    v = row.get(key)
    if v is None:
        return None
    v = str(v).strip()
    if not v or v.lower() in ("nan", "none"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- outcome loading ---------------------------------------------------------
def load_local_results(repo_root: Path) -> Dict[str, Dict[Tuple[str, str], int]]:
    """date -> {(away,home): home_won(0/1)} from docs/data/_results_*.json."""
    out: Dict[str, Dict[Tuple[str, str], int]] = {}
    for p in sorted((repo_root / "docs" / "data").glob("_results_*.json")):
        try:
            j = json.load(open(p, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        date = j.get("date") or re.search(r"(\d{4}-\d{2}-\d{2})", p.name).group(1)
        games = j.get("games", []) if isinstance(j, dict) else j
        day: Dict[Tuple[str, str], int] = {}
        for g in games:
            if not isinstance(g, dict):
                continue
            hr = g.get("home_runs", g.get("home_score"))
            ar = g.get("away_runs", g.get("away_score"))
            if hr is None or ar is None or hr == ar:  # skip ties/unplayed
                continue
            day[(_norm(g.get("away")), _norm(g.get("home")))] = 1 if hr > ar else 0
        if day:
            out[date] = day
    return out


def fetch_statsapi(date: str) -> Dict[Tuple[str, str], int]:
    """Fallback: pull finals for one date from statsapi. Only on --fetch."""
    import time
    import urllib.request
    url = (f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
           "&hydrate=team")
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                j = json.load(r)
            break
        except Exception:
            time.sleep(0.3)
    else:
        return {}
    day: Dict[Tuple[str, str], int] = {}
    for d in j.get("dates", []):
        for g in d.get("games", []):
            t = g["teams"]
            if g.get("status", {}).get("detailedState", "") not in (
                    "Final", "Game Over", "Completed Early"):
                continue
            hs, as_ = t["home"].get("score"), t["away"].get("score")
            if hs is None or as_ is None or hs == as_:
                continue
            a = _norm(t["away"]["team"].get("abbreviation"))
            h = _norm(t["home"]["team"].get("abbreviation"))
            day[(a, h)] = 1 if hs > as_ else 0
    return day


# ---- assemble the joined ledger ---------------------------------------------
class Row:
    __slots__ = ("date", "away", "home", "pick_is_home", "home_won",
                 "pick_won", "p_model", "full_prob", "fair", "staked", "tier")


def build_ledger(repo_root: Path, since: Optional[str], until: Optional[str],
                 fetch: bool) -> List[Row]:
    results = load_local_results(repo_root)
    ledger: List[Row] = []
    fetched_cache: Dict[str, Dict[Tuple[str, str], int]] = {}
    for path in sorted((repo_root / "docs" / "data").glob("picks_*_diag.csv")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
        if not m:
            continue
        date = m.group(1)
        if since and date < since:
            continue
        if until and date > until:
            continue
        day = results.get(date)
        if day is None and fetch:
            day = fetched_cache.get(date) or fetch_statsapi(date)
            fetched_cache[date] = day
        if not day:
            continue
        for rec in csv.DictReader(open(path, encoding="utf-8", errors="replace")):
            mm = re.match(r"\s*([A-Z]+)\s*@\s*([A-Z]+)", rec.get("matchup", ""))
            if not mm:
                continue
            away, home = _norm(mm.group(1)), _norm(mm.group(2))
            if (away, home) not in day:
                continue
            pick = (rec.get("pick") or "").strip()
            if not pick or pick == "TBD":
                continue
            p_model = _num(rec, "p_model") or _num(rec, "pick_prob")
            full_prob = _num(rec, "full_prob")
            if p_model is None and full_prob is None:
                continue
            r = Row()
            r.date, r.away, r.home = date, away, home
            r.pick_is_home = (_norm(pick) == home)
            r.home_won = day[(away, home)]
            r.pick_won = r.home_won if r.pick_is_home else (1 - r.home_won)
            r.p_model = p_model
            r.full_prob = full_prob
            r.fair = _num(rec, "fair_prob")           # pick-perspective
            why = (rec.get("why_skipped") or "").strip()
            r.tier = (rec.get("tier") or "").strip()
            r.staked = (why == "" and r.tier not in ("", "SKIP"))
            ledger.append(r)
    return ledger


# ---- metrics (pure python, no sklearn dependency) ---------------------------
def _auc(preds: List[float], ys: List[int]) -> float:
    pos = [p for p, y in zip(preds, ys) if y == 1]
    neg = [p for p, y in zip(preds, ys) if y == 0]
    if not pos or not neg:
        return float("nan")
    # rank-based Mann-Whitney U with tie handling
    paired = sorted(zip(preds, ys), key=lambda t: t[0])
    ranks = [0.0] * len(paired)
    i = 0
    while i < len(paired):
        j = i
        while j + 1 < len(paired) and paired[j + 1][0] == paired[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # 1-based average rank
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    sum_pos = sum(rk for rk, (_, y) in zip(ranks, paired) if y == 1)
    n_pos, n_neg = len(pos), len(neg)
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _murphy(preds: List[float], ys: List[int], n_bins: int = 10
            ) -> Tuple[float, float, float]:
    """Return (reliability, resolution, uncertainty) via 10-bin decomposition."""
    n = len(ys)
    base = sum(ys) / n
    unc = base * (1 - base)
    rel = res = 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [i for i, p in enumerate(preds)
               if (p >= lo and (p < hi or (b == n_bins - 1 and p <= hi)))]
        if not idx:
            continue
        nk = len(idx)
        pbar = sum(preds[i] for i in idx) / nk
        obar = sum(ys[i] for i in idx) / nk
        rel += nk * (pbar - obar) ** 2
        res += nk * (obar - base) ** 2
    return rel / n, res / n, unc


def _cal_slope_intercept(preds: List[float], ys: List[int]) -> Tuple[float, float]:
    """Logistic regression of y on logit(p): slope 1 / intercept 0 = calibrated."""
    eps = 1e-6
    x = [math.log(min(1 - eps, max(eps, p)) / (1 - min(1 - eps, max(eps, p))))
         for p in preds]
    a, b = 0.0, 0.0  # intercept, slope; Newton-Raphson
    for _ in range(50):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for xi, yi in zip(x, ys):
            z = a + b * xi
            pr = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            g0 += (pr - yi)
            g1 += (pr - yi) * xi
            w = pr * (1 - pr)
            h00 += w
            h01 += w * xi
            h11 += w * xi * xi
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a -= da
        b -= db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return b, a


def score(preds: List[float], ys: List[int]) -> dict:
    n = len(ys)
    eps = 1e-15
    base = sum(ys) / n
    brier = sum((p - y) ** 2 for p, y in zip(preds, ys)) / n
    logloss = -sum(y * math.log(max(eps, p)) + (1 - y) * math.log(max(eps, 1 - p))
                   for p, y in zip(preds, ys)) / n
    acc = sum(1 for p, y in zip(preds, ys)
              if (p >= 0.5) == (y == 1)) / n
    rel, res, unc = _murphy(preds, ys)
    slope, intercept = _cal_slope_intercept(preds, ys)
    return {"n": n, "mean_pred": sum(preds) / n, "base_rate": base,
            "acc": acc, "brier": brier, "logloss": logloss,
            "auc": _auc(preds, ys), "reliability": rel, "resolution": res,
            "uncertainty": unc, "cal_slope": slope, "cal_intercept": intercept}


def _perm_resolution_p(preds: List[float], ys: List[int], reps: int = 2000
                       ) -> Tuple[float, float]:
    """Permutation test: P(shuffled resolution >= observed). Deterministic LCG
    so the result is reproducible without importing random (harness bans it)."""
    _, obs, _ = _murphy(preds, ys)
    y = list(ys)
    seed = 1234567
    ge = 0
    null_sum = 0.0
    n = len(y)
    for _ in range(reps):
        for i in range(n - 1, 0, -1):
            seed = (1103515245 * seed + 12345) & 0x7FFFFFFF
            j = seed % (i + 1)
            y[i], y[j] = y[j], y[i]
        _, r, _ = _murphy(preds, y)
        null_sum += r
        if r >= obs - 1e-12:
            ge += 1
    return (ge + 1) / (reps + 1), null_sum / reps


# ---- predictor construction --------------------------------------------------
def pick_frame(ledger: List[Row]) -> List[Row]:
    return [r for r in ledger if r.p_model is not None]


def evaluate(ledger: List[Row], blend_weights: List[float]) -> dict:
    out: dict = {"n_total": len(ledger)}

    # ---- PICK framing: predict P(pick wins) ----
    pf = [r for r in ledger if r.p_model is not None]
    if pf:
        ys = [r.pick_won for r in pf]
        base = sum(ys) / len(ys)
        preds_model = [r.p_model for r in pf]
        block = {"framing": "pick", "n": len(pf),
                 "model": score(preds_model, ys),
                 "constant": score([base] * len(pf), ys)}
        pp, pnull = _perm_resolution_p(preds_model, ys)
        block["model"]["resolution_perm_p"] = pp
        block["model"]["resolution_null_mean"] = pnull

        # market + blends need fair_prob present
        mk = [r for r in pf if r.fair is not None]
        if mk:
            ys_m = [r.pick_won for r in mk]
            base_m = sum(ys_m) / len(ys_m)
            block["market_subset_n"] = len(mk)
            block["market"] = score([r.fair for r in mk], ys_m)
            block["model_on_market_subset"] = score([r.p_model for r in mk], ys_m)
            block["constant_on_market_subset"] = score([base_m] * len(mk), ys_m)
            blends = {}
            for w in blend_weights:
                pb = [w * r.p_model + (1 - w) * r.fair for r in mk]
                s = score(pb, ys_m)
                blends[f"{w:.2f}"] = {"brier": s["brier"], "logloss": s["logloss"],
                                      "auc": s["auc"], "resolution": s["resolution"],
                                      "cal_slope": s["cal_slope"]}
            block["blend_sweep"] = blends
        out["pick"] = block

    # ---- HOME framing: predict P(home wins) ----
    hf = [r for r in ledger if r.full_prob is not None]
    if hf:
        ys = [r.home_won for r in hf]
        base = sum(ys) / len(ys)
        block = {"framing": "home", "n": len(hf),
                 "model": score([r.full_prob for r in hf], ys),
                 "constant": score([base] * len(hf), ys),
                 "always_home": score([1.0 - 1e-9] * len(hf), ys)}
        out["home"] = block

    # ---- subgroups (pick framing) ----
    subs = {}
    staked = [r for r in pf if r.staked]
    if staked:
        ys = [r.pick_won for r in staked]
        subs["staked"] = score([r.p_model for r in staked], ys)
    skipped = [r for r in pf if not r.staked]
    if skipped:
        ys = [r.pick_won for r in skipped]
        subs["skipped"] = score([r.p_model for r in skipped], ys)
    # favorite vs underdog by market
    fav = [r for r in pf if r.fair is not None and r.fair >= 0.5]
    dog = [r for r in pf if r.fair is not None and r.fair < 0.5]
    if fav:
        subs["market_favorite"] = score([r.p_model for r in fav],
                                         [r.pick_won for r in fav])
    if dog:
        subs["market_underdog"] = score([r.p_model for r in dog],
                                         [r.pick_won for r in dog])
    out["subgroups"] = subs
    return out


# ---- reporting ---------------------------------------------------------------
def _fmt(s: dict) -> str:
    return (f"n={s['n']:4d}  base={s['base_rate']:.3f}  acc={s['acc']:.3f}  "
            f"Brier={s['brier']:.4f}  logloss={s['logloss']:.4f}  "
            f"AUC={s['auc']:.3f}  resol={s['resolution']:.4f}  "
            f"slope={s['cal_slope']:+.3f}")


def report(ev: dict) -> str:
    L = []
    L.append("=" * 92)
    L.append("BASELINE EVALUATION — is the model better than trivial predictors?")
    L.append("=" * 92)

    if "pick" in ev:
        b = ev["pick"]
        L.append(f"\n[PICK framing: predict P(picked side wins)]  N={b['n']}")
        L.append(f"  model      {_fmt(b['model'])}")
        pp = b['model'].get('resolution_perm_p')
        if pp is not None:
            verdict = ("NO measurable resolution (inside noise band)"
                       if pp > 0.05 else "resolution is significant")
            L.append(f"             resolution perm-p={pp:.3f} "
                     f"(null mean {b['model'].get('resolution_null_mean',0):.4f}) "
                     f"-> {verdict}")
        L.append(f"  constant   {_fmt(b['constant'])}")
        won = b['model']['brier'] < b['constant']['brier']
        L.append(f"  >> model {'BEATS' if won else 'LOSES TO'} the constant "
                 f"on Brier ({b['model']['brier']:.4f} vs {b['constant']['brier']:.4f})")
        if "market" in b:
            L.append(f"\n  --- market subset (fair_prob present, "
                     f"n={b['market_subset_n']}) ---")
            L.append(f"  market     {_fmt(b['market'])}")
            L.append(f"  model      {_fmt(b['model_on_market_subset'])}")
            L.append(f"  constant   {_fmt(b['constant_on_market_subset'])}")
            mb = b['market']['brier']
            mm = b['model_on_market_subset']['brier']
            L.append(f"  >> on identical games, market Brier {mb:.4f} vs "
                     f"model {mm:.4f} — {'MARKET wins' if mb < mm else 'model wins'}")
            L.append("\n  --- market blend sweep  p = w*model + (1-w)*market ---")
            L.append("       w   Brier    logloss   AUC     resol   slope")
            for w, s in b["blend_sweep"].items():
                L.append(f"     {w}  {s['brier']:.4f}  {s['logloss']:.4f}  "
                         f"{s['auc']:.3f}  {s['resolution']:.4f}  {s['cal_slope']:+.3f}")
            best = min(b["blend_sweep"].items(), key=lambda kv: kv[1]["brier"])
            L.append(f"  >> best blend w={best[0]} (Brier {best[1]['brier']:.4f}); "
                     f"w=1.00 is model-only, w=0.00 is market-only")

    if "home" in ev:
        b = ev["home"]
        L.append(f"\n[HOME framing: predict P(home wins)]  N={b['n']}")
        L.append(f"  model      {_fmt(b['model'])}")
        L.append(f"  constant   {_fmt(b['constant'])}")
        L.append(f"  always_home{_fmt(b['always_home'])}")

    if ev.get("subgroups"):
        L.append("\n[SUBGROUPS — model, pick framing]")
        for k, s in ev["subgroups"].items():
            L.append(f"  {k:18s} {_fmt(s)}")

    L.append("\n" + "=" * 92)
    L.append("Reading: resolution ~ 0 with perm-p > 0.05 means the model does NOT "
             "separate\nstrong from weak games. A staked-subset AUC < 0.50 means the "
             "bets are anti-selected.")
    L.append("=" * 92)
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="only slates on/after YYYY-MM-DD")
    ap.add_argument("--until", help="only slates on/before YYYY-MM-DD")
    ap.add_argument("--staked-only", action="store_true",
                    help="restrict to would-be staked picks")
    ap.add_argument("--fetch", action="store_true",
                    help="pull finals from statsapi for dates with no local file")
    ap.add_argument("--blend-weights", default="0,0.25,0.5,0.75,1.0",
                    help="comma list of model weights for the blend sweep")
    ap.add_argument("--json", help="also write a JSON summary to this path")
    args = ap.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    ledger = build_ledger(repo_root, args.since, args.until, args.fetch)
    if args.staked_only:
        ledger = [r for r in ledger if r.staked]
    if len(ledger) < 30:
        print(f"Only {len(ledger)} graded rows found — too few to evaluate. "
              f"(Need docs/data/picks_*_diag.csv joined to _results_*.json; "
              f"try --fetch.)")
        return 1

    weights = [float(w) for w in args.blend_weights.split(",")]
    ev = evaluate(ledger, weights)
    print(report(ev))

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        json.dump(ev, open(args.json, "w"), indent=2)
        print(f"\nWrote summary -> {args.json}")
    return 0


if __name__ == "__main__":
    logging_level = os.environ.get("LOGLEVEL")
    sys.exit(main())
