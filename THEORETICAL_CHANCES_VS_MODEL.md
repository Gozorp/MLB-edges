# Theoretical Chances vs. F5% / Full% — What They Are and Why the "Toy" Looks More Confident
**mlb_edge dashboard explainer · 2026-06-04**

## TL;DR
- **F5% and Full%** are your real, **calibrated** XGBoost models. They drive the picks and the grades, and they are deliberately *conservative* because a single MLB game is mostly variance.
- **Theoretical chances** is — by its own label on the card — a **"hypothetical toy model · not the pick."** It's an independent, from-scratch run simulation, fully isolated from the real model.
- The toy usually *looks* sharper/more confident for two structural reasons: it is **uncalibrated**, and it uses only **three inputs**. **More confident ≠ more accurate.**
- Use **F5%/Full% for anything that touches money.** Treat **Theoretical chances as a curiosity / cross-check**, never a tiebreaker.

---

## 1. The three numbers on the card

| Column | Source | Role |
|---|---|---|
| **F5%** | First-five-innings XGBoost model (`f5_prob`) | Real, calibrated. Feeds the F5 product and the Stage-1/2 gap signal. |
| **Full%** | Full-game XGBoost model (`full_prob`) | Real, calibrated. The win probability the **pick and grade ride on**. |
| **Theoretical chances** | `mlb_edge/theoretical_chances.py` (ported to JS in `docs/index.html`) | A hypothetical structural simulation. Explicitly **not the pick**. |

The gap between F5% and Full% (`f5_full_delta`, the "Stage-1/2 gap") is itself a real signal the pipeline uses — it measures how much a game's edge lives early (starter-driven) vs. late (bullpen/lineup-driven). Both halves are calibrated model outputs.

---

## 2. How each is computed

### The real models — F5% and Full%
- **XGBoost classifiers** trained on historical game outcomes.
- **Full feature set:** starter xERA/SIERA, bullpen quality, lineup quality vs. handedness, pitch-quality index (PQI), park, weather, umpire tendencies, and more.
- **Probability calibration** is applied (`models/calibration_v1.json`) so that a stated 62% wins close to 62% of the time over a large sample.
- The result is intentionally **regressed toward 50%** — the model is honest about how little is knowable about any single game.

### The toy — Theoretical chances
A faithful from-scratch simulation, computed lazily when you expand a row (so it costs the slate nothing). The steps:

1. Start from a **league-average plate-appearance distribution** `[out, BB, 1B, 2B, 3B, HR] = [0.690, 0.085, 0.140, 0.045, 0.004, 0.036]`.
2. **Tilt it by the opposing starter's K%** (a Log5 + James-Stein-style shrinkage, compressed) plus a tiny bounded offense modifier from lineup concentration.
3. Simulate a half-inning with a **24 base-out-state Markov simulation**, **1,200 Monte Carlo trials**, to build each team's per-inning run distribution.
4. **Convolve nine innings** into a full-game run distribution.
5. Apply a small **bullpen-gap leverage** tilt (`hl_bullpen_xwoba_gap`).
6. Compute win probability by comparing the two run distributions, with an **honest 0.5 split on ties**.

**Inputs it uses: only three** — the opposing starter's K%, lineup concentration, and the bullpen xwOBA gap. That is the entire input set. No xERA, no PQI, no park/weather/umpire, and — critically — **no training against real outcomes.**

---

## 3. Why the toy looks MORE confident (and why that's misleading)

Three structural reasons, none of which make it right:

**(a) It has never been calibrated.** Nothing has ever checked whether its 70% actually wins 70%. The real models were tuned against thousands of historical games; the toy was not. Uncalibrated probabilities naturally drift toward the extremes.

**(b) It uses three narrow inputs.** With only K%, lineup concentration, and the bullpen gap, a single factor — a high-strikeout starter, say — swings the whole number hard and produces a decisive-looking result. The real model dilutes that one factor against dozens of others, which pulls the number back toward the middle.

**(c) The real model is deliberately humble.** A single baseball game is dominated by irreducible variance, so the calibrated model regresses toward 50% on purpose. A model that is honest about that uncertainty will *always* look less confident than a toy that isn't.

So when Theoretical chances says **68%** and Full% says **56%**, that 12-point gap is the toy's **overconfidence**, not extra insight.

---

## 4. Why this actually matters (the trap)

This isn't academic. The loss post-mortem (`LOSSES_POSTMORTEM_2026-05-08_to_06-02.md`) found that **overconfidence was the single biggest driver of losing bets** — the 0.9447 PLATINUM "calibration artifact," the negative-edge GOLD confirms, the single-signal picks. Every one of those lost because a number *looked* more certain than reality supported.

The Theoretical-chances card is, by construction, exactly that kind of number: uncalibrated, narrow-input, prone to looking sharp. Trusting it over the calibrated model would re-introduce the precise failure mode the system has spent real effort eliminating. That's why it ships labeled "hypothetical · not the pick."

---

## 5. How to use each

| Number | Trust for decisions? | What it's for |
|---|---|---|
| **Full%** | **Yes** | The actual pick + grade; the win probability money rides on. |
| **F5%** | **Yes** | The first-5 product and the Stage-1/2 (F5-vs-Full) gap signal. |
| **Theoretical chances** | **No** | A curiosity / independent cross-check only. |

**Reading a divergence:** when the toy diverges sharply from Full%, read it as *"the toy is overweighting one factor"* — not *"the model is missing something."* A large gap is a flag that the toy is overconfident on that game. Occasionally it can prompt a worthwhile question (e.g., a genuinely extreme strikeout matchup), but the answer comes from the calibrated model, not the toy.

---

## 6. Bottom line

**"More confident" is not "more accurate."** F5% and Full% are the honest, calibrated, outcome-validated numbers, and they are what the system bets on. Theoretical chances is a labeled toy — a from-scratch simulation on three inputs with zero calibration — and its apparent sharpness *is* the very overconfidence the real model is built to avoid. Keep it as a cross-check, not a tiebreaker.
