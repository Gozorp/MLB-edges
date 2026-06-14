# Today's Model Situation — 6/14/2026 (rev. 2)

**The question:** is the model's *calculation* off, or is this just a bad day?

**Strongest honest answer:** The **directional engine has not failed**, and the executive SKIP layer is doing real risk control. But this is **not** "just variance, move along." It's a genuine ~10-day cold stretch, and — more importantly — the model's **confidence scale is suspect, especially in the upper bands.** The disciplined move is to hold the freeze, finish the pre-registered probe, and apply probability shrinkage **only if** the July evidence confirms the overconfidence pattern. Treat calibration as a serious queued fix, not a hand-wave.

---

## 1. Today (6/14) — mixed, low exposure

Today by itself is unremarkable: of the games already final the directional picks are roughly even, several are still live. **Correction (verified 2026-06-14):** the **staked** book is not "1–0" as an earlier draft said — it is **0–0**. Across the entire frozen era (6/4–6/14) the executive layer placed **zero** moneyline bets: every DIAMOND/PLATINUM tier pick (13 of them) was independently killed by the Layer-2 edge gate (edge outside [4,15]pp), and GOLD/SKIP carry stake_mult 0 by design. So the cold stretch is **entirely paper** (directional picks); there is no live P&L behind it. "Today" is not the story — the recent *run* is.

## 2. The cold stretch is real — quantified

Frozen-model out-of-sample ledger (the locked record since the 6/4 freeze):

| Slate | Record | Win% | Brier |
|-------|:------:|:----:|:-----:|
| 6/05 | 5–10 | 33% | 0.259 |
| 6/06 | 3–12 | 20% | 0.229 |
| 6/07 | 6–9 | 40% | 0.243 |
| 6/08 | 4–4 | 50% | 0.211 |
| 6/09 | 9–6 | 60% | 0.258 |
| 6/10 | 6–9 | 40% | 0.176 |
| 6/11 | 5–3 | 62% | 0.181 |
| 6/12 | 6–9 | 40% | 0.173 |
| 6/13 | 7–8 | 47% | 0.230 |
| 6/14 | in progress | — | — |
| **Pooled** | **52–71** | **42%** | **0.219** |

42% over ~123 picks, against a long-run rate of **54.2%** (520 graded picks, 4/27–6/9). That is ugly. It is **not just one bad day** — it's a real 10-day cold patch, and it deserves to be named as such.

## 3. Is it broken calculation or bad luck? — the math, stated honestly

The model is a deliberately **thin-edge favorite-identifier**: ~54% long-run, only ~4 points above a coin flip and barely above the ~53–54% base rate of just taking the favorite. With an edge that thin, short-run results are variance-dominated, and the numbers here are *compatible with* a thin-edge process running cold:

- **Pooled drawdown:** at a true 54% rate, expected wins over 123 picks ≈ 66; observed 52, with a standard deviation of ~5.5 wins. So this is roughly **2.5 SD below expectation.** That is rare **in a pre-specified sample** — but much less shocking once you remember we are *scanning many overlapping rolling windows across a full MLB season*, where a 2–2.5 SD window will surface somewhere without the underlying method having changed. It is not proof of failure; it is also not nothing.
- **The probability layer is not obviously collapsing — but I won't call it "intact."** The pooled Brier is still respectable at **0.219**, and several days (6/10–6/12) ran a strong 0.17–0.18. But a couple of days reached **~0.258–0.259**, at or just past the 0.25 coin-flip reference. So: not a collapse, but not pristine either.

**Conclusion:** this is consistent with a cold stretch, not a broken directional method. But that's only half the picture — see §4.

## 4. The real meat: direction vs. calibration

This is the part that matters most. The model's flaw is **not** that it picks the wrong sides — it's that it **overstates how much better its side is.** Long-run it *predicts* ~57.9% and *delivers* ~54.2%, with the gap widening on its highest-conviction picks (the 75–80% band has been a net loser).

> The model may still know which side is better — but it is overstating *how much* better. The **ranking layer may be salvageable while the staking/confidence layer needs shrinkage.**

That's why a "78% TB" loss (6/13, TB 0–8) feels catastrophic: an inflated 78% sets an expectation the true ~60-something% never justified. The sides aren't the problem; the **scale** is. This is a calibration defect, and it's the single most actionable thing about this whole stretch.

## 5. Calibration approach for July — shrinkage first, isotonic last

When the July re-test runs, the method order should match the failure mode (upper-band overconfidence), and respect the small sample (~350 picks, thin in each high-prob band):

1. **First: logit shrinkage / Platt-style** — `calibrated_p = sigmoid(a + b·logit(raw_p))`. A fitted slope `b < 1` naturally pulls 70–80% picks back toward the middle, which is exactly the observed defect. One parameter, hard to overfit.
2. **Second: beta calibration** — if the miscalibration is asymmetric and Platt is too rigid.
3. **Last: isotonic / binned-isotonic** — powerful but easy to overfit with small, uneven per-band samples. Use only if the reliability curve shows clear non-linear distortion *and* there's enough data per band.
4. **Non-negotiable:** fit on one sample, evaluate on another. Do **not** use the same ~350 July picks to both discover the fix and declare victory.

> Note on the locked spec: `CALIBRATION_SPEC.md` currently leads with binned-isotonic and treats Platt as a "known loser." That verdict came from the 2024–25 test, which fit on **noisy inner-CV OOF probabilities** — a data-quality artifact where Platt over-regressed. On the **clean frozen production probs**, a gentle logit shrink targeting the specific upper-band mode is the more principled first try. Reordering the bake-off to lead with logit-shrinkage is a refinement worth making **before any fit** (so it stays pre-registration-clean). This is now recorded as a dated, pre-registered amendment — `CALIBRATION_SPEC_ADDENDUM_2026-06-14.md` — with the original locked spec left fully intact.

## 6. The SKIP layer is useful — but not a clean bill of health

Almost everything in this stretch was SKIP, and the staked book was **empty — 0 bets placed across 6/4–6/14** (verified). That's the executive layer protecting bankroll during a rough directional period — arguably *too* well (see the audit's GOLD-clean question). **But a SKIP system can keep P&L looking safe while the underlying signal quietly degrades.** So the SKIP argument is necessary, not sufficient. The open question isn't "did SKIP dodge the bad picks?" — it's:

> **Are the rare non-SKIP (staked) picks still beating their expected value, after calibration?**

That requires its own separate audit of the staked subset, not just the all-picks directional record. (Today's staked n is far too small to answer it — pure telemetry for now.)

## 7. Blowouts need their own lens — margin ≠ moneyline error

TB at 78% losing is a probability question; TB losing **0–8** is a *margin* question, and the two should not be conflated. A true 78% team loses 22% of the time, and some of those losses are ugly by pure chance. Blowout severity only indicts the model if its high-confidence favorites lose **by large margins more often than comparable market favorites** in the same probability band. Proposed probe (complements the already-locked July "blowout over-favor" study):

> Among model picks above ~65%, compare the **loss-margin distribution** to market-implied favorites in the same probability band.

That cleanly separates "baseball produces ugly losses" from "the model specifically under-prices tail-risk blowouts." Two disciplines on this, both now written into the addendum: (1) bin by **no-vig market-implied** probability, not the model's own, or you compare apples to oranges; and (2) treat any feature-engineering response (day-of pitcher, bullpen, lineup, player-level volatility vs. team-history priors) as a **hypothesis queue, not a confirmed diagnosis** — the current evidence supports calibration overconfidence far more than any specific feature defect, so no feature ships unless the probe comes back RED. Full design, market definition, and Green/Yellow/Red rule: `CALIBRATION_SPEC_ADDENDUM_2026-06-14.md`.

## 8. Tripwires for *actual* broken calculation (none tripped yet)

- Directional accuracy below 50% over a **large** sample (hundreds, not 10 days). *(Long-run 54%.)*
- Brier sustained above ~0.26. *(Pooled 0.219; worst days ~0.258.)*
- Calibration gap widening materially beyond the known ~4pp. *(July re-test measures this cleanly.)*
- The pre-registered probes (blowout over-favor; upper-band edge decay; the §7 margin probe) confirming structural bias. *(Gated to July; ledger only ~53 of the 350 picks needed.)*

## Bottom line

Don't unfreeze the model over this stretch, and don't rewrite the directional engine — it hasn't failed. **But don't file the cold patch under "just variance" either.** The model's confidence scale is the suspect component, especially in the upper bands, and that's a real, addressable defect. Hold the freeze, let the pre-registered probe finish, and apply probability shrinkage **only if** the July evidence confirms the overconfidence — fit and validated on separate samples. The directional read may be fine; the *certainty* attached to it is what needs discipline.
