# SP Micro-Feature Retrospective Backtest — PRE-REGISTRATION

**Status:** LOCKED 2026-06-14, BEFORE any harness code is written (integrity requirement).
**Class:** read-only research. NO change to the frozen XGBoost win/totals model, weights, stake layer, or production chain. Informs the July retrain go/no-go only.
**Question:** Do the new SP micro-features (short-term recency, dominance/CSW proxy, HR floor) carry *incremental* out-of-sample predictive value **beyond what the frozen model's win probability already captures** (the model is ~0.60 SP-anchored)? If yes → green-light a full-power July re-test + gated retrain. If no/harm → don't.

---

## Data
- **Baseline + games:** diag CSVs `docs/data/picks_*_diag.csv`, span 2026-04-27 → 2026-06-15 (49 slates). Analyzable set = games with BOTH starters named (non-TBD) AND a model `full_prob`: **n ≈ 308 games**.
- **Outcome:** home-team win (0/1) from statsapi schedule finals per slate date (DH-aware key `away@home@G#`).
- **Model baseline probability:** diag `full_prob`, oriented to the home team via `pick_side` (`home_prob = pick_prob if pick_side==home else 1−pick_prob`).
- **Features (recomputed AS-OF each slate, leakage-safe — gameLog strictly BEFORE slate date):** per starter then expressed as home−away diffs so they're win-relative:
  - `d_roll3_k` = home_SP − away_SP rolling-3-start K% (CSW proxy / dominance)
  - `d_kbb` = home_SP − away_SP season K-BB%
  - `d_hr9` = away_SP − home_SP season HR/9 (higher = home edge)
  - `d_l3_era_trend` = away_SP − home_SP (last-3-start ERA − season ERA)  (opponent SP fading = home edge)
  - `d_xfip` = away_SP − home_SP xFIP (higher away xFIP = home edge)
  - Features standardized (z-scored) on the training fold only.
  - **Deferred to July v2 (NOT in this run):** opponent-specific 3yr-decayed HR rate, and TRUE rolling CSW% / kill-pitch matchup (Savant). Stated so we don't quietly add them later.

## Method
- **B0 (baseline):** walk-forward logistic `logit(home_win) = α + β·logit(home_prob)`.
- **B1 (candidate):** B0 + the standardized feature diffs above (L2-regularized logistic).
- **Walk-forward OOS:** chronological; expanding-window train on all games strictly before the test date, predict each test date's games; burn-in = first ~120 games before scoring begins. Accumulate OOS predictions for both B0 and B1 over the identical scored game set.

## Pass / Fail bar (locked — ALL must hold to PASS)
1. **Log-loss:** Δ = LL(B0) − LL(B1) ≥ **+0.002**, AND a 1000-sample game-level bootstrap 95% CI for Δ **excludes 0**.
2. **Sign-correct coefficients:** walk-forward-averaged β for each feature matches its baseball-expected sign (all five defined positive toward home win); no dominant feature sign-flipped.
3. **No tail-variance inflation:** among confident predictions (|p−0.5| > 0.30), B1 error rate ≤ B0 error rate; and B1 worst single-game log-loss ≤ B0 + 0.05.
- **Secondary (report only, non-gating):** Brier delta, AUC delta, reliability/calibration.

## Decision rule (power-aware)
- **n is modest (~300):** minimum detectable Δlog-loss here is ~0.01–0.02; a true 0.002–0.005 ROI-scale effect will likely read NULL. So:
  - **PASS** → "PROMISING — GREENLIT" for a full-power re-test on the season-long ledger in July, *then* the gated XGBoost retrain (its own OOS bar: DeLong-significant log-loss + thick |ΔAUC| ≤ 0.01 + sign-correct importances). NOT an immediate retrain.
  - **NULL** (CI includes 0, signs not clearly wrong) → inconclusive; re-test in July with more data. No model change.
  - **KILL** (Δlog-loss < 0 / harm, OR signs wrong) → drop this feature set; do not pursue in July.
- We feed the **underlying signals**, never the heuristic tier tags, and keep them only if the machine-learned weights pass the bar (avoids the heuristic feedback-loop the user flagged).

## Integrity
This file is committed BEFORE the harness (`tools/backtest_sp_features.py`). Thresholds above are frozen; any change after seeing results requires a dated amendment in this file, not a silent edit.

---

## RESULTS — run 2026-06-15 (full results log: `logs/backtest_sp_features_2026-06-15.log`)

**Sample:** 308 diag games with both SP + model prob; 296 scored vs statsapi finals (281 full-feature, 15 partial-imputed); 165 OOS predictions after the 120-game walk-forward burn-in.

**Baseline validation (done before trusting any verdict):** diag `pick_side` is blank in all rows, so `full_prob` is read directly as the home-win probability — confirmed correct: raw `full_prob` AUC = 0.5486 over 583 games (orientation is NOT inverted). **However**, on the both-SP date-tail that the OOS test actually scores, raw `full_prob` AUC = **0.504 (n=176) — essentially coin-flip.** The frozen model shows ~no discrimination on this particular slice (vs its ~54% directional over its full history).

**Numbers vs the locked bar:**
1. Log-loss Δ (B0−B1) = **+0.0102**, bootstrap 95% CI **[−0.019, +0.037] → includes 0** ⇒ NOT significant. **FAIL gate 1.**
2. Signs: d_roll3_k +, d_kbb +, d_hr9 + (correct); **d_l3_trend − and d_xfip − (WRONG-SIGN).** **FAIL gate 2.**
3. Tail: worst single-game log-loss B0 0.821 → B1 1.431. **FAIL gate 3.**
Secondary: Brier Δ +0.0054; AUC B0 0.439 → B1 0.579 (B0 fitted below 0.5 reflects the near-coin-flip slice, not a wiring bug).

**VERDICT: DOES NOT PASS → no green light for a July retrain on this evidence.** Mechanically the script returns KILL (sign rule), but the most accurate scientific reading is **NULL / fails-to-validate**: the OOS slice carried almost no baseline signal (AUC 0.504) and n is below the power needed for a 0.002–0.005 effect, so the wrong-signs are not a trustworthy "harm" signal — they're what collinear features do on a near-random, underpowered slice.

**Decision (locked rule honored, not rescued):** the model is **protected** — the data does not justify feeding these features in now. **Re-test in July** with: (a) the season-long ledger (more games, windows where the model shows its normal edge); (b) single-feature / orthogonalized tests to avoid collinear sign-flips; (c) re-confirm baseline discrimination on the eval window before interpreting deltas. Only then consider the gated XGBoost retrain. No model change made.
