# Calculation Review — 2026-07-09 (read-only audit, freeze intact)

**Ask:** "check over the code and see if the calculation can be improved."
**Method:** read the live math end-to-end (devig → edge → conviction → caps →
Kelly), then numerically verify today's published slate against re-computation.
**Headline:** the chain is *implemented correctly* — every number re-derives
exactly. The genuine improvement levers already exist as pre-registered July
jobs; nothing here justifies touching the frozen model early.

---

## 1. Verified correct (with evidence, 2026-07-08 slate)

| Piece | Check | Result |
|---|---|---|
| `edge_pp = (p_model − fair) × 100` | recomputed from published `p_model`/`fair_prob` | **14/14 priced games exact** (≤0.06pp rounding) |
| Kelly chain | `quarter = raw/4`, `full = min(raw, 0.25)`, `eighth = raw/8` | **all rows consistent**; formula `(bp−q)/b` standard; guards: `dec≤1→0`, `raw≤0→0`, 5%-bankroll clamp, quarter-Kelly default, daily risk cap (10u travel value) |
| Shin devig (`market_analysis.shin`) | synthetic two-way books | sums to 1.0000; symmetric book → exactly 0.5; favorite shifted **up** vs proportional (+2.7pp at 1.50/2.60, +5.4pp at 1.20/4.80) = correct favorite-longshot handling |
| Degenerate-book guard | live 7/5 finals (1.005/100 odds) | caught by the sanity cap → treated as missing odds ✓ |
| Kalshi anchor price | `kalshi_odds.py` | uses **mid(yes_bid, yes_ask)** → last → ask fallback priority (better than last-trade-only) ✓ |
| Away-pick sign flips | `edge_calculator` perspective builder | SP/bullpen gap signs flipped, luck + sample-size columns swapped (the v10/v11 audit fixes are present) ✓ |
| Train-time leakage guard | `model.py train_stage2_full` | active (`|rho|` gate over stage-2 features) ✓ |
| Band coherence | config vs grader vs display | `MIN/MAX_EDGE_PCT = 0.04/0.15` == the caps' [4,15]pp window == the Goldilocks banner (4-8-15) == the new edge heat bands ✓ |

## 2. Findings

**F1 — stale comment (trivial, behavior fine).** `edge_calculator.py`'s v8
comment still says the profitable band is "[5pp, 10pp]"; config has been
[4pp, 15pp] for some time and everything downstream uses config. Fix is a
comment edit inside a model-adjacent file → **post-freeze hygiene**, not now.

**F2 — the single highest-leverage improvement is ALREADY pre-registered.**
Kelly and edge consume **raw, uncalibrated `p_model`**, which the dashboard
itself shows is tail-overconfident (predicts ~57.9%, wins ~54.2%). Overstated
p inflates both `edge_pp` and Kelly stakes. The quarter-Kelly fraction, 5%
clamp, [4,15] band and daily cap blunt the damage — but the *correct* fix is
the **July calibration bake-off (CALIBRATION_SPEC.md, locked 06-09)**: if the
binned-isotonic calibrator beats RAW under the block-bootstrap bar, sizing and
edges improve mechanically with zero new code here. Do not pre-empt it.

**F3 — bullpen-fatigue blind spot (the one real calculation *hole*, already
pre-registered).** The June attribution study found model overconfidence
concentrates in fatigued-pen games (+19.5pp gap, n=19 vs +2.1pp rested,
n=73). Candidate features are built + validated offline
(`tools/bp_fatigue_features.py`, FI 5.4→38.1 by tier); promotion is gated by
**BULLPEN_FATIGUE_PREREG.md** (July walk-forward, locked gates).

**F4 — portfolio Kelly (new, low priority, July-only idea).** Stakes are
sized per-game independently; the daily risk cap is a blunt portfolio
constraint. A simultaneous-Kelly (or simple same-slate scaling) study could be
pre-registered post-return. Expected gain is small — same-day ML outcomes are
weakly correlated — file it behind F2/F3.

**F5 — Shin closed form (micro, no action).** Two-outcome Shin has a closed
form; the fixed-point loop converges to the same place. Note only.

**F6 — input-data quality (known, deferred July).** FanGraphs stuff+ 403s and
the Savant spin/arsenal CSV export are dead (memory `reference_sp_feeds_degraded`);
primary SP anchor stays healthy on Savant expected-stats. Multi-endpoint
repair is the July ticket — it improves *inputs*, not formulas.

## 3. Do NOT re-litigate (all failed OOS or were rejected with data)
`pick_prob ≥ 0.55` confidence floor (corr edge↔prob is +0.38; wrong lever) ·
the 3 executive stake-gates (33-43% OOS precision vs 85% bar; killed winners) ·
the post-GOLD-0-4 four changes (all reject/null on 196 games; cd=True picks are
the model's BEST group) · min-5 D/SKIP backfill (37%-win traps) · per-team
penalty weights (banned by standing decision).

## 4. Already-queued July validation pipeline (the real "improvement" roadmap)
Calibration bake-off (F2) · bullpen-fatigue promotion (F3) · weather×HR
interaction retrain (shadow branch `feature/weather-hr-retrain`, gated) ·
blowout over-favor recalibration probe · SP-role feature study · emp-Bayes
SP-xERA AUC test · OOS Brier tracker · incoherence-bucket read
(`tools/incoherence_audit.py`, n-gated) · totals bottom-up rebuild
(TOTALS_REBUILD_PLAN.md + signed OOS protocol).

> **Bottom line:** today's calculations do what they claim, verified to the
> decimal. The capacity for improvement is real but lives in the pre-registered
> July queue — first the calibrator (fixes sizing + edges in one shot), then
> bullpen fatigue (the one measured systematic error), then data-feed repair.
> Shipping any of it early would break the freeze for gains the backtest
> discipline hasn't earned yet.
