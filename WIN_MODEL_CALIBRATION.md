# Win-Model Calibration — design & file map (how the model's win probability is calibrated)

> **The one-sentence answer:** the production moneyline pick currently ships **RAW**
> XGBoost probabilities — every probability-calibration layer in the repo is built but
> **OFF/dormant**, by deliberate decision (raw beat every calibrator tested). The only
> live "calibration"-named process recalibrates *feature weights*, not probabilities, and
> it is itself frozen right now. Re-enabling a probability calibrator is a pre-registered
> **July** decision (`CALIBRATION_SPEC.md`). This doc maps the files and the data flow.

---

## 1. How the winning team is determined (the path the calibration sits in)

The win model is **two-stage**, both XGBoost, both producing a **home-perspective** probability:

- **Stage 1 — `f5_prob`** (first-5-innings / starter-centric). Its raw probability is **not
  calibrated** — it is consumed by Stage 2 *as a feature*, where a ranking-only signal is
  what Stage 2 wants (`mlb_edge/model.py`, comment at lines 83–84).
- **Stage 2 — `full_prob`** (full-game). This is the number that picks the team.

Pick rule (pick-perspective):
```
full_prob >= 0.5  ->  pick = HOME
full_prob <  0.5  ->  pick = AWAY
p_model / pick_prob = the probability from the PICK's perspective (so always >= 0.5-ish)
edge_pp = (p_model - fair_prob) * 100      # model vs devigged market
```
`pick_prob` is an explicit alias of `p_model` (`main_predict.py` ~line 431). A derived
signal, `f5_full_delta = |f5_prob - full_prob|`, measures **Stage-1/Stage-2 disagreement**
(`main_predict.py` ~lines 421–429) and is the trigger for the incoherence bucket (§5).

**Calibration is the step that would remap that raw `full_prob` to its empirical hit rate
before it becomes `pick_prob`. Today that step is a pass-through.**

---

## 2. The three things called "calibration" in this repo (don't conflate them)

| # | Mechanism | File(s) | State today | What it acts on |
|---|-----------|---------|-------------|-----------------|
| 1 | **In-booster Stage-2 calibration** | `mlb_edge/model.py` (`ENABLE_STAGE2_CALIBRATION`), `mlb_edge/calibration.py` (`BinnedIsotonicCalibrator`) | **DISABLED** (`= False`) | the probability |
| 2 | **Post-bake probability remap** | `mlb_edge/post_calibrator.py` + `models/calibration_v1.json` | **DORMANT** (not imported in `main_predict`) | the probability |
| 3 | **Self-learn weight recalibration** | `mlb_edge/auto_weight_update.py :: apply_calibration_from_all_picks` | **FROZEN** (`--skip-weights`) | the feature *weights*, NOT the probability |

Plus a set of **executive guardrails** that exist *because* #1 and #2 are off (§4).

---

## 3. The two probability calibrators (both off) — and WHY

### 3a. In-booster Stage-2 calibration — `model.py` + `calibration.py`
`ENABLE_STAGE2_CALIBRATION: bool = False` (`model.py` line 85). When True, a calibrator is
fit on held-out Stage-2 predictions; `calibration.py`'s `BinnedIsotonicCalibrator` bins the
raw prob, Bayes-shrinks each bin toward its midpoint via a `Beta(prior_alpha)` prior (the
small-n tail regularizer), then runs a weighted PAVA monotone pass. **Built, never enabled.**

Why off (documented verdict, `model.py` lines 70–84, from a 3,580-game 2024–25 walk-forward):
raw beat every calibrator on every metric (Brier 0.2466 raw vs 0.2481 best Platt; decile |err|
0.0222 raw vs 1.77–2.67× worse). The mis-calibration is a **zigzag, not a smooth sigmoid**, so
a 2-parameter Platt can't capture it; the richer binned-isotonic "could try, but the raw numbers
already beat anything we've fit."

### 3b. Post-bake remap — `post_calibrator.py` + `calibration_v1.json`
A standalone loader that reads a binned table (`models/calibration_v1.json` — `n_samples=551`,
`n_bins=10`, `beta_prior=8.0`, `fit_date=2026-06-13`) of `(bin_mid -> calibrated_rate)` and
**linearly interpolates** a raw prob to its empirical rate. Designed to shrink tail overconfidence
*without retraining the booster*. Note: the table **is re-fit daily** (the cron keeps it fresh —
hence today's `fit_date`), but it is **consumed by nothing**: `post_calibrator` is **NOT imported
or called in `main_predict.py`**, so the fresh table never touches production `pick_prob`. It is
**intentionally fail-open** (missing/bad JSON -> pass-through, logged at DEBUG) and staged for the
§6 re-test.

---

## 4. What IS live: the self-learn + the executive guardrails

### 4a. `apply_calibration_from_all_picks` — recalibrates WEIGHTS, not probabilities
`mlb_edge/auto_weight_update.py` (def at line 255). Despite the name, this is the daily
**self-learn over conviction-signal feature weights**, and it resolves the open audit question
in `CALIBRATION_SPEC.md` §1/§8: **it does not remap probabilities**, so it cannot double-shrink
with a future probability calibrator. Mechanism, per pick:

```
residual   = won - p                      # p = pick_prob/p_model/full_prob (first present)
tier_weight = TIER_LEARN_WEIGHT[tier]
if stress_warnings OR confidence_downgrade:  tier_weight *= STRESS_MASK_FACTOR   # 0.3x
for each conviction signal in the pick's `signals`:
    for each feature in SIGNAL_TO_FEATURES[signal]:
        feature_grad[feature] += tier_weight * residual
# then, per feature:
new = cur * (1 + learn_rate * feature_grad[feature]/n_with_signals)
new = clip(new, floor = MIN_RELATIVE_WEIGHT*base, ceil = base * NEW_CEILING_MULT)   # 0.25x .. 1.5x
```

Safeguards (memory `feedback_selflearn_safeguards`, shipped 2026-05-25):
asymmetric **ceiling `base*1.5`** (lets an under-credited weight recover, not just decay),
**stress-mask 0.3×** (down-weights games the model itself flagged), and a **warm-up gate**
(`historical < WARMUP_THRESHOLD` -> *audit-only*, no state mutation). Writes
`data/state/weights_state.json`. **Currently frozen** for the SFO/Japan window (`--skip-weights`;
the `weights_state` freshness sentinel is intentionally yellow — see memory
`reference_healthcheck_local_publish`).

### 4b. Executive caps standing in for the absent probability calibrator
Because raw probabilities are known to over-extend in the high tail, the executive grade layer
applies calibration-motivated **caps** instead of a smooth calibrator (`main_predict.py` ~line
1167; `parlay_builder.py`): **HARD CAP** at edge `> +25pp`, and a **`calibration_caution_18_25pp`**
soft band over the +18→+24pp bucket (too few losses to hard-SKIP, enough to caution). Separately,
`mlb_edge/stress_test.py` forces `confidence_downgrade` when a tier's rolling 30-day **Brier
residual std** is too noisy (or history is insufficient — fail-conservative). These are guardrails,
not calibration; `CALIBRATION_SPEC.md` §9.4 flags them for re-tuning if a real calibrator ever ships.

---

## 5. The incoherence bucket (a calibration *audit*, not a calibrator)
Pre-registered 2026-06-11 (`CALIBRATION_SPEC.md` addendum): a descriptive OOS audit of games
whose `grade_reasons` contains "Stage 1/2 disagree" (the grade engine's own delta ≥ 0.12 trigger).
It asks where calibration error concentrates (in- vs out-of-bucket Brier), which stage leaks alpha
(F5-favored vs full-favored side win rate), and whether the model or the market is closer on
high-edge bucket games. **Changes nothing by itself** — hypothesis-generating only.

---

## 6. The live decision in front of all this — `CALIBRATION_SPEC.md` (July)
The companion file `CALIBRATION_SPEC.md` is the **pre-registered re-test** (locked 2026-06-09,
runs after the freeze lifts ≈2026-07-14) that decides whether to finally turn a probability
calibrator on. It fits the binned-isotonic candidate on the **frozen-model OOS ledger**
(`oos_ledger.jsonl`, `slate_date >= 2026-06-04`), bakes it off against **RAW** (the reigning
champion) under a walk-forward, **day-grouped block-bootstrap** acceptance bar (ΔBrier 95% CI
strictly > 0, ECE non-inferior, log-loss within +0.002, n_OOS ≥ 250). PASS -> wire via exactly
one path (set `TrainedModel.calibrator` + flip the flag, **or** call `PostCalibrator` in
`main_predict`, never both). NULL -> keep raw. That spec is the authority for the *plan*; this
doc is the authority for the *current implementation*.

---

## 7. File map (quick reference)
- `mlb_edge/model.py` — `ENABLE_STAGE2_CALIBRATION = False` (line 85) + the 2024–25 verdict.
- `mlb_edge/calibration.py` — `BinnedIsotonicCalibrator` (built, disabled).
- `mlb_edge/post_calibrator.py` — post-bake remap loader (dormant, fail-open, not wired).
- `models/calibration_v1.json` — the dormant table (n=551, 10 bins; re-fit daily, consumed by nothing).
- `mlb_edge/auto_weight_update.py` — `apply_calibration_from_all_picks` (weight self-learn, frozen).
- `mlb_edge/weights_state.py` — `SIGNAL_TO_FEATURES` map + `weights_state.json` I/O.
- `mlb_edge/stress_test.py` — tier Brier-residual `confidence_downgrade`.
- `mlb_edge/main_predict.py` — pick rule, `pick_prob`/`f5_full_delta`, executive caps (~1167).
- `CALIBRATION_SPEC.md` — the pre-registered July re-test + incoherence-bucket addendum.
- `oos_ledger.jsonl` (`docs/data/`) — the frozen-era calibration sample the re-test will use.

> **Bottom line:** today the winning team is chosen from the **raw** Stage-2 `full_prob` (≥0.5),
> tempered only by executive caps and (when unfrozen) a weight-level self-learn. Every smooth
> probability calibrator is built and waiting, deliberately off because raw still wins — and the
> July pre-registered test is the gate that decides if that ever changes.
