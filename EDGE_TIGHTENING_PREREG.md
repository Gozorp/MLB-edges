# Edge / Trap-Detection Tightening — Pre-Registered July Roadmap

**Status:** PLANNING ONLY, captured 2026-06-20. Nothing here is built or deployed. Every item changes the live edge/stake path or adds a feed/cron → **all gated to the post-Japan July window**, behind time-separated-holdout validation. Production + frozen model untouched.
**Companions:** §3 ≈ `CALIBRATION_SPEC.md` (already locked); validation harness ≈ `SP_FEATURE_BACKTEST_PREREG.md`; engineering source `edge_calculator.py` / `config.py` (see `EDGE_FEATURE_EXPLAINED.md`).

---

## Locked methodology (applies to EVERY item below — do not relax)
1. **Time-separated holdout only.** Train 2021–2023 (or available), validate 2024, walk-forward 2025→2026. No in-sample tightening — that's how we overfit our own backtest.
2. **Select on Sharpe-of-ROI, not raw ROI.** Raw ROI pushes toward tiny lucky slices.
3. **≥150 bets per slice** before a number is trusted.
4. **Pre-register thresholds before fitting**; changes after seeing results require a dated amendment, not a silent edit.
5. Ship only what clears the bar; NULL → keep current production.

---

## Priority order (user-set) + my honest refinements

### P1 — Calibrate `p_model` before computing edge  (user §3)
`edge = p_model − fair` is only meaningful if `p_model` is calibrated. Add Platt/isotonic trained on out-of-fold predictions, applied before `edge_calculator.py:425`.
- **This is already pre-registered** as `CALIBRATION_SPEC.md` (binned-isotonic vs RAW on the frozen OOS ledger, July test). The new piece the user adds: once it passes, apply the calibrated prob *upstream of the edge calc* and then **loosen the `MIN_MODEL_PROB 0.48 / MAX 0.72` band** (the band exists to patch miscalibration). Fold this into the CALIBRATION_SPEC bake-off as an explicit secondary arm.
- Deliverable: reliability diagram pre/post. If the curve is already near-diagonal, calibration is NOT the bug and we don't ship it (the prior OOS calibration test landed NULL — keep that skepticism).

### P2 — Capture closing-line value (CLV)  (user §4)
The known gap: `fair_prob` is build-time only. Re-scrape Kalshi/ESPN at ~first pitch → store `fair_prob_close`; `clv = fair_prob_close − fair_prob_open` for the picked side.
- Can't use CLV at build time, but it's (a) the key **training label/feature for the trap detector**, and (b) a live **kill switch**: `clv < −0.02` → down-rank/kill before first pitch.
- **Vehicle:** the staged `t30_watch` / SP-release watcher pattern (a T-30 re-scrape), NOT a new always-on daemon. Build on the `feat/t30-rolling-scheduler` branch; same enable-on-return discipline.
- Honest note: Kalshi is build-time today and the-odds-api is dead, so the close re-scrape needs the same feed plumbing — verify the close snapshot is reliably fetchable before depending on it.

### P3 — Active trap-detector classifier  (user §2)
Replace the passive "outside the band = trap" with a binary classifier on label `bet_lost AND edge_was_high`, features: `edge_pp`, `fair_prob`, `model_prob`, **CLV/line-movement (from P2)**, SP xERA/SIERA/FIP-vs-edge-direction, bullpen fatigue, vig (P4-style), rest/travel. Calibrate; gate `p_trap < threshold` (backtest the threshold).
- **Hard blocker, stated honestly:** we do not have enough labeled outcomes to train this yet. The edge gates have kept the book **dormant all freeze (0 staked)**, and the historical staked sample is small. Fix the label scope first: train on **all band-eligible candidates graded vs. actual outcome** (not just *staked* bets) so n is usable — otherwise this overfits instantly. Reverse-line-movement + public% are the sharpest features but **need feeds we don't have** (no public%, no live line history); P2 supplies the only movement signal we can actually source.
- Order: P2 must land first (CLV is the headline feature), and P1 (so `model_prob`/edge inputs are honest).

### P4 — Finer Goldilocks re-slice  (user §1)
Grid `MIN_EDGE_PCT∈{.04,.05,.06,.07,.08}`, `MAX_EDGE_PCT∈{.10,.12,.13,.15}`, `MIN_FAIR_PROB∈{.42,.45,.48,.50}`, `MIN_MODEL_PROB∈{.48,.50,.52}`, `MAX_MODEL_PROB∈{.68,.70,.72}` on the walk-forward harness; pick max Sharpe with ≥150 bets/slice.
- **Pushback on the stated prior.** The plan's prior is "MIN_EDGE 0.05–0.06," but `config.py:141` documents the opposite finding: **0.04 beat 0.05 by ~12× pooled ROI (+4.50% vs +0.37%), WR 57.5% vs 52.2%, positive in both 2024 and 2025.** Don't anchor to a prior the existing walk-forward already contradicts — let the Sharpe re-slice decide, and if it says 0.05–0.06, demand that it beats the documented 0.04 result on the *same* holdout. **Re-run AFTER P1**: calibrating `p_model` shifts the whole edge distribution, so any band tuned pre-calibration is stale.

### P5 — Conviction-tier tightening  (user §5)
Require **N-of-5 mutually-exclusive signal families** {pitching, offense, bullpen, rest/travel, market-movement} — DIAMOND ≥4/5 families, not raw signal count (decorrelates xERA+SIERA+FIP that all come from one pitcher). Add per-signal minimum **effect-size** thresholds. Cap DIAMOND frequency ≤5% of plays; shrink the multiplier until DIAMOND ROI ≥ 2× GOLD on holdout.
- This is the cleanest win that doesn't need new data — but it touches `score_conviction` (core path), so July + holdout-gated.

### Lower priority (P6–P8)
- **§6 Bayesian shrinkage on edge** — hierarchical (team,SP,venue) prior shrinking edges toward 0; principled replacement for the hard `MAX_EDGE_PCT` cap. Attractive but data-hungry; revisit once P1–P4 are in and there's more labeled history.
- **§7 Ensemble + directional-agreement gate** — second structurally-different model (logistic / run-diff target); stake only on agreement. High-precision trap signal; biggest build cost → last.
- **§8 Vig as a feature/slice** — `total_vig = p_home_raw + p_away_raw − 1`; test the bimodal-ROI hypothesis; feed P3. Cheap to add to the backtest grid; do it alongside P4.

---

## Endorsed guardrails (user's "what not to do" — locked)
- Do **not** lower `MAX_EDGE_PCT` "to be safe" without a re-backtest — that shrinks sample without fixing the calibration root cause.
- Do **not** add conviction signals without decorrelating the existing ones (more correlated signals ≠ more information).
- Do **not** trust any tightening not validated on a time-separated holdout.

## Sequencing summary
P1 (calibration) → P2 (CLV capture) → P3 (trap detector, fed by P1+P2) → P4 (re-slice band, post-calibration) → P5 (conviction families) → P8 (vig, with P4) → P6/P7 (shrinkage, ensemble). Each is a separate pre-registered experiment with the locked methodology above; production stays frozen until a change clears its holdout bar.

---

## Addendum A — Freeze-safe prep executed 2026-06-20

All four items below are **offline / docs / planning only** — no model, no `docs/data`, no cron, no live path touched. They exist so July is "run experiments," not "write boilerplate."

### A1 — Shadow eligible-candidate ledger (P3 scaffold) — BUILT
`offline_sim/build_shadow_eligible_ledger.py` → `offline_sim/shadow_eligible_ledger.csv`. Every diag game with a market line + final, recording picked-side `model_prob / fair_prob / edge_pp / model_tier`, the Goldilocks-band `is_eligible` flag, and `pick_won`. **This is the P3 label-scope fix in concrete form: all band-eligible candidates graded, not just staked bets.**

First run (2026 OOS, 2026-04-27 → 06-19):
- **449** graded candidates with a market line; **139** band-eligible, win% **0.554**; all-candidates win% 0.543.
- Edge-bucket win% (the P4/trap signal, *small-n, directional only*): 4–6pp **0.543** (n=35) · 6–8pp **0.643** (n=42) · 8–10pp **0.389** (n=18) · 10–15pp **0.545** (n=44). **Non-monotonic** — a real dip in the 8–10pp band, which is exactly what the P4 re-slice + P3 trap detector are meant to catch.
- **Honest caveat:** n=139 eligible over ~2 months is a *scaffold, not a verdict* — every bucket is under the ≥150-bets/slice bar. The 2023–2025 extension (needed for power) requires the walk-forward backtest cache / `grid_search` infra and is a **July sandbox job**, not buildable from current files. Do not tune anything off these buckets.

### A2 — CLV schema (P2 prep) — LOCKED
When the P2 re-scrape lands on the `feat/t30-rolling-scheduler` branch, the ledger gains exactly these columns, no schema churn later:
- `fair_prob_open` — existing build-time `fair_prob` (Shin de-vig of Kalshi at slate build).
- `fair_prob_close` — T-30 re-scrape of the **same** market, run through the **same** `market_analysis.shin` de-vig (identical function — no second de-vig implementation).
- `clv_diff` = `fair_prob_close − fair_prob_open` for the **picked side**. Positive = market moved toward us (good); `clv_diff < −0.02` = the live kill-switch / top trap feature.
- `clv_captured_ts` — UTC timestamp of the close snapshot (provenance; null until P2 runs).
No DB exists (CSV/JSON storage), so "pre-allocate columns" = these names are reserved here; the parser reuses Shin and the existing Kalshi fetch — verify the close snapshot is reliably fetchable before P3 depends on it.

### A3 — Sharpe-of-ROI definition (P4 selection metric) — LOCKED
To remove July ambiguity, "select on Sharpe-of-ROI" is fixed as **daily-ROI Sharpe**:
1. For each calendar day in the holdout, `daily_roi(d) = (sum of unit P/L on that day's slice bets) / (units staked that day)`. **A day with no qualifying bets contributes `daily_roi = 0`** (penalizes thin slices that sit idle, and dilutes streaky ones).
2. `Sharpe = mean(daily_roi) / stdev(daily_roi)` over all holdout days (population stdev; **not annualized** — it is a *relative* score to rank slices on one common holdout, not a reported financial Sharpe).
3. Ties / `stdev = 0` (e.g. a slice that never bets) → Sharpe undefined → slice rejected.
4. Sharpe is the **selector**; the **≥150-bets/slice** gate and "must beat the documented 0.04 band on the same holdout" requirement (see P4) still bind. Raw pooled ROI is reported alongside but never the selector.

### A4 — Conviction signal→family map (P5 prep) — BUILT
`offline_sim/conviction_signal_families.yaml`, mapped from `score_conviction` source. Findings that change P5:
- Only **3** of the 5 target families actually fire today: **pitching** (F1 + savant gate + dropped F4), **offense** (F2 *and* F3), **bullpen** (F5). **rest/travel and market-movement have zero signals.**
- **Mutual-exclusivity violation:** F2 (team xwOBA) and F3 (swing/take) are *both* offense, each +1 under raw-count tiering → an F1+F2+F3 game scores "3 signals → DIAMOND" while spanning only **2** distinct families. Collapse F2+F3 into one offense vote.
- **Structural consequence:** the proposed "DIAMOND = ≥4-of-5 families" is **impossible today** (only 3 families exist) — P5 *depends on first adding* a rest/travel family and a market-movement family (the latter = CLV from P2). Interim tightening that needs no new data: require DIAMOND = 3 **distinct** families (not 3 raw signals), which already deflates the inflated count.
- Per-signal **effect-size floors** (user §5) are stubbed in the YAML as TODOs to be set from the July holdout, distinct from the existing trigger thresholds.
