# CALIBRATION_SPEC.md — Probability Calibration Re-Test (frozen OOS)

**Status:** PRE-REGISTERED 2026-06-09 (Architecture Pre-Flight Rule 2). Freeze-safe planning
doc only — **no code runs until the SFO/Japan freeze lifts (≈2026-07-14).** Thresholds and
partition are locked below *before* any fit. No retroactive tuning.

---

## 0. One paragraph

The production model currently ships **raw** XGBoost probabilities (`ENABLE_STAGE2_CALIBRATION
= False`). The dashboard shows the raw booster is overconfident on 2026 data (predicts 57.9%,
wins 54.2% over n=450; overconfidence concentrates above 60%, the 75–80% bucket lost on n=11).
The repo already contains the calibrator we'd want (`mlb_edge/calibration.py :
BinnedIsotonicCalibrator`, Bayes-shrunk binned isotonic) but it is **disabled** because a
3,580-game 2024–25 walk-forward test found **raw beat every calibrator on every metric**. This
spec pre-registers a clean re-test: fit the binned-isotonic calibrator on the **frozen-model OOS
ledger (6/4 onward)** and decide — under a locked walk-forward, block-bootstrap acceptance bar —
whether it now beats raw. PASS → wire it in. NULL → keep raw, exactly as 2024–25 concluded.

---

## 1. The incumbent + prior verdict — READ FIRST

This is not a greenfield build. Current state in the repo:

- `mlb_edge/model.py` — `ENABLE_STAGE2_CALIBRATION = False`. In-booster calibration is OFF.
  Documented verdict (2024–25, 3,580 honestly-OOF games):

  | Metric        | Raw    | Platt (leaky last-15%) | Platt (OOF inner-CV) |
  |---------------|--------|------------------------|----------------------|
  | Brier         | 0.2466 | 0.2514 (+0.005)        | 0.2481 (+0.002)      |
  | Log loss      | 0.686  | 0.698 (+0.012)         | 0.689 (+0.003)       |
  | Decile \|err\|| 0.0222 | 0.0594 (2.67x)         | 0.0394 (1.77x)       |
  | Sharpness     | 0.0481 | 0.1054 (2.19x)         | 0.0391 (0.81x)       |

  Verdict: *"Raw beats both calibrators on every metric … mis-calibration is a zigzag, not a
  smooth sigmoid … a richer calibrator (binned isotonic with smoothing, or per-bin shrinkage)
  could try, but the raw numbers already beat anything we've fit."*

- `mlb_edge/calibration.py` — `BinnedIsotonicCalibrator`: bins raw prob, Bayes-shrinks each bin
  toward its midpoint via a Beta(`prior_alpha`) prior (this is the small-n tail regularizer —
  more principled than a static hard cap), then a weighted PAVA monotonic pass. **Built, never
  validated/enabled in production.**
- `mlb_edge/post_calibrator.py` + `models/calibration_v1.json` (n_samples=503, 10 bins) — a
  post-bake binned remap loader. **Dormant: `post_calibrator` is NOT imported or called in
  `main_predict.py`.** It does not touch production `pick_prob`.
- `auto_weight_update.apply_calibration_from_all_picks` — the active daily self-learn. Audit
  required (see §8): confirm whether it mutates *weights* only, or also a probability map. If the
  latter, it must not stack with this calibrator (double-shrink risk).

**Implication:** the bar to clear is **RAW**, the reigning champion — not the old Platt fits.

## 2. Why re-test now (what is actually different)

The 2024–25 test fit calibrators on **inner-CV OOF** probabilities. Those boosters saw less data
than the production booster, so their raw probs were noisier and Platt correctly pulled them
toward the base rate — but that flattening did not generalize to the sharper production booster.

Since 2026-06-04 the weights are **frozen**. The OOS ledger logs the **production booster's own
raw `pick_prob`** on **truly-unseen** games. That is a cleaner, single-model calibration sample
than 2024–25 ever had — the first fair test of the "richer calibrator" the verdict left open.

## 3. Dataset (LOCKED)

- Source: `docs/data/oos_ledger.jsonl`, rows where `phase == "result"`.
- Filter: `slate_date >= 2026-06-04` (frozen era only — see §4).
- Inputs: `pick_prob` = pre-executive, pre-cap raw model prob. Label: `outcome ∈ {0,1}`.
- Exclusions: postponed/suspended games; any row whose `weights_frozen` signature ≠ the frozen
  booster sig; doubleheaders keyed `${away}@${home}@G${n}` (no bare-key collisions).
- Dedupe: one row per game.
- **Minimum-n gate:** ≥ 350 graded frozen picks before any production fit (expect ~480 by 7/14).
  Below 350 → extend burn-in / wait; do not fit.

## 4. The weight-shift trap (why May/June pre-6/4 is excluded from the fit)

Pre-6/4 picks came from a continuously-updating model. Calibrating across them fits the *average*
miscalibration of a dozen different boosters — uninformative about the frozen booster. The
pre-freeze history is used ONLY to (a) prototype the harness and (b) rank methods relatively
(§6). It NEVER trains the production curve.

## 5. Candidate calibrators (bake-off)

- **C0 — RAW** (control / incumbent; the thing to beat).
- **C1 — BinnedIsotonicCalibrator** (existing). Grid: `n_bins ∈ {8,10,12}`,
  `prior_alpha ∈ {10,20,40}`.
- **C2 — C1 + hard tail clamp**: `clip(cal, 0.05, CAP)`, `CAP ∈ {0.65,0.68,0.70}`. Belt-and-
  suspenders on the sparse tail (prior_alpha already shrinks it; this tests whether an explicit
  ceiling adds anything).
- **C3 — Platt (sigmoid)**: included ONLY as the known-loser reference, to confirm the harness
  reproduces the 2024–25 result (a sanity check on the harness, not a real contender).

Within-family selection by walk-forward OOS Brier; **simplest-wins tie-break** (fewer knots /
larger `prior_alpha` / no clamp preferred). No method advances on in-sample fit.

## 6. Partition protocol (LOCKED)

- **Walk-forward, expanding window. GROUP BY game-day** — a day is never split across train/test.
- Burn-in: first **200** frozen graded picks before the first OOS calibrated prediction.
- For each subsequent day `t`: fit the candidate on all graded picks from days `< t`; predict
  day-`t` picks; store `(raw_prob, cal_prob, outcome, day)`. Score only finalized games.
- Produces **paired** OOS predictions (raw vs cal on identical picks).
- **No random k-fold.** Same-day games share weather/ump/slate-wide variance; a random split
  puts correlated rows on both sides and flatters the calibrator (the exact leak that makes a bad
  calibrator look good).

## 7. Acceptance bar (PRE-REGISTERED — locked before any fit)

PASS requires **ALL** of:

- **(a)** `ΔBrier = Brier_raw − Brier_cal > 0` with the **95% block-bootstrap CI strictly above 0**
  (lower bound > 0). See §8.
- **(b)** Expected calibration error (ECE, 10 equal-mass bins) on pooled OOS: `ECE_cal ≤ ECE_raw`
  (non-inferior; ideally strictly lower).
- **(c)** Log loss non-inferior: `logloss_cal ≤ logloss_raw + 0.002` (the 2024–25 tolerance band).
- **(d)** `n_OOS ≥ 250` scored walk-forward picks.

Else → **NULL**: keep RAW, `ENABLE_STAGE2_CALIBRATION` stays False, nothing ships. No partial
ship, no "directionally promising" override. Record the result (PASS or NULL) in memory either way.

## 8. Block bootstrap — the CI logic (LOCKED)

The CI in §7(a) is computed by **block bootstrap with the game-day as the resampling unit**, NOT
the individual pick.

- Let `D` = the list of OOS days; each day carries its vector of paired `(raw, cal, y)` picks.
- For `b = 1..B` (`B = 10,000`): sample `|D|` days **with replacement**; pool their picks;
  compute `ΔBrier_b = mean((raw−y)²) − mean((cal−y)²)` over the resampled pool.
- 95% CI = `[2.5th, 97.5th]` percentile of `{ΔBrier_b}`. **PASS iff the 2.5th percentile > 0.**
- Report: point `ΔBrier` (real sample), median, CI, `B`, `n_days`, `n_picks`, fixed RNG `seed`.
- **Why block, not iid:** resampling individual picks ignores within-day correlation, understates
  variance, narrows the CI, and manufactures false PASSes. Day-level blocks are the honest version
  — the bootstrap analogue of the §6 anti-leak rule.

## 9. If PASS — integration (LOCKED)

1. Re-fit the winning calibrator on the **full** frozen pool once; serialize to
   `models/calibration_v2_isotonic.json` with: knots, `n_bins`, `prior_alpha`, clamp, training
   window `[start,end]`, `n`, `fit_ts`, and **`booster_weights_sig`** (the calibrator is
   invalidated by any future booster retrain and must be re-fit after one).
2. Wire via **exactly one** path — either set `TrainedModel.calibrator` + flip
   `ENABLE_STAGE2_CALIBRATION = True`, OR call `PostCalibrator` in `main_predict`. Never both
   (double-calibration). The calibrator sits **between raw Stage-2 output and `pick_prob`, before**
   the executive cap/parlay layer.
3. **Reconcile with `apply_calibration_from_all_picks`** (gating sub-task): audit what it mutates.
   If it also remaps probabilities, disable that remap so the two don't double-shrink.
4. Re-evaluate the existing executive flags built on the *premise* of an un-validated isotonic
   calibrator — `main_predict` SOFT CAP 6.5 / `calibration_caution_18_25pp` (~line 1167). With a
   validated calibrator they may be redundant or need retuning.
5. **Shadow one slate** before flipping the live `pick_prob`: log raw vs cal side-by-side in the
   ledger, eyeball, then enable.

## 10. Non-goals / braid separation

NOT a booster retrain. NOT feature surgery (log-transform high-end xwOBA, bullpen-decay audit =
a separate post-Japan, OOS-gated track — different risk profile, do not conflate). NOT the
executive cap/parlay layer. NOT totals. This is one post-hoc mathematical overlay, validated in
isolation.

## 11. Execution checklist (July)

- [ ] Confirm `n_OOS ≥ 350` in the ledger; snapshot/freeze the pool.
- [ ] Build `tools/calibration_backtest.py` — walk-forward + block bootstrap, **read-only**, no
      model mutation, no writes to `data/state/`.
- [ ] Run C0–C3; emit a reliability report + per-candidate `ΔBrier` CIs.
- [ ] Apply §7 verbatim. PASS → §9 wiring. NULL → record + stop.
- [ ] Sanity check: C3 (Platt) should still lose, reproducing the 2024–25 verdict on the harness.
- [ ] If shipped: shadow one slate, then enable; re-tune §9.4 executive flags.

## Appendix — reproduce the 2024–25 numbers

Before trusting any new result, run the harness on the 2024–25 OOF set and confirm it reproduces
the §1 table (raw beats Platt). A harness that cannot reproduce the known verdict cannot be
trusted to adjudicate the new one.


---

## INCOHERENCE BUCKET ADDENDUM (pre-registered 2026-06-11, LOCKED)

Registered BEFORE any 2026-06-11+ results were examined (Rule 2). Motivated by
the 6/11 SEA@BAL case: pick_prob 0.668 vs market fair 0.485 (edge_pp 18.31),
F5/full inversion (0.312/0.668 home-ref), grade_reasons carrying
"Stage 1/2 disagree (delta=0.19)"; executive layer graded D/SKIP.

**Bucket definition (no new constants):** predict-phase games whose diag
`grade_reasons` contains "Stage 1/2 disagree" -- i.e., the grade engine's own
penalty trigger fired (delta >= 0.12 since the 2026-05-10 tightening from
0.15, per parlay_builder.py). The bucket inherits whatever threshold the
engine used on the day the row was produced; the reason string in the
archived diag is the flag.

**Data:** OOS window 2026-06-04 -> freeze lift. Join oos_ledger.jsonl
(predict / result / f5_result phases) with archived docs/data/picks_<d>_diag.csv
for grade_reasons (the ledger does not carry reasons; diags are retained).

**Pre-registered questions (descriptive audit; hypothesis-generating only):**
1. Calibration split: Brier + accuracy of pick_prob (and f5_prob via the F5
   leg) in-bucket vs out-of-bucket.
2. Which stage leaks alpha: within the bucket, full-game win rate of the
   F5-favored side vs the full-favored side (f5_prob = Stage-1 proxy,
   full_prob = Stage-2 proxy; both logged since ledger deployment).
3. Market interaction: in-bucket subset with |edge_pp| >= 15 -- which is
   closer to realized outcomes, fair_prob (market) or pick_prob (model)?

**Discipline:** This audit changes NOTHING by itself -- no gate, weight,
cap, or staking rule moves on its findings. Any actionable result requires
its own pre-registered probe meeting the standing override bar (n >= 10,
precision >= 85% to keep). Directional claims require bucket n >= 25; if the
window is thinner, extend the window rather than the claim. Anchor case to
re-grade first: 2026-06-11 SEA @ BAL.
