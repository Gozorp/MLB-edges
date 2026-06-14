# Totals Product — Rebuild Plan
**Status: PAUSED as of 2026-06-03.** Do not bet totals, do not maintain totals infra, until this plan executes.

## Why it's paused
The totals run model (`pred_runs`) carries essentially no signal. On 118 graded games (5/14–6/02) its correlation with actual total runs is **r = 0.05 (R² = 0.3%)** — worse than predicting the league mean — while the market line reaches **r = 0.33**. The pre-registered recalibration backtest (constant / linear / isotonic, OOS via K-fold + walk-forward) returned **NULL**: you cannot calibrate a predictor that carries no signal. The model is also badly under-dispersed (pred SD ≈ 0.5–1.1 vs market 1.87 vs actual 4.18) — it regresses every game toward ~9 runs. Full detail: memory `project_totals_recal_prereg` + `LOSSES_POSTMORTEM_2026-05-08_to_06-02.md`.

## Step 0 — Restore a market line feed (MANDATORY, before any modeling)
A rebuilt model is only validatable against a real market total (CLV). **All free, keyless sources are confirmed DEAD as of 2026-06-03 — do NOT re-litigate this:**
- the-odds-api paid subscription lapsed 2026-05-21.
- ESPN public lines page returns an HTTP **202** challenge (JS-gated) — scrape yields nothing.
- DraftKings public JSON returns HTTP **403** Access Denied (Akamai anti-bot), even from a residential IP.

Bypassing modern sportsbook anti-bot requires rotating **residential proxies**, which cost more than a clean key — so "free scraping" is a false economy.

**Decision: use a FREE-TIER API KEY.** the-odds-api **"Starter"** — 500 calls/month, **no credit card**, email signup only. Budget ≈ 16 calls/day; polling twice (AM anchor + pre-first-pitch CLV) covers the full MLB slate without nearing the cap. **Cost: $0.** The adapter is ~5 minutes to wire (the dead `odds_totals.py` consumer + existing totals schema make it a drop-in). Moneyline is unaffected — it already has a free keyless anchor in **Kalshi**.

## Step 1+ — Structural bottom-up run model
Replace the flat regressor with `total = E[home runs] + E[away runs]`, each projected from levers already in the pipeline:
- **SP run-prevention:** regressed SIERA/xFIP (empirical-Bayes shrinkage on thin samples), projected IP, K%/barrel-against.
- **Bullpen run-prevention** for the residual innings, weighted by rest/usage.
- **Lineup run-creation vs the actual arm:** Log5 / odds-ratio on the real 9 hitters' xwOBA vs the SP's handedness, then vs the pen.
- **Multi-year regressed park factor; weather** (temp, wind vector, air density — disproportionately important for totals); **umpire** HP K%/BB%; **catcher framing / OAA.**

Aggregate via the **existing Monte Carlo / Bivariate-Poisson engine**, but **fix the under-dispersion**: verify each input actually varies game-to-game, let the output spread toward the market's ~1.9-run SD, and ablation-test each component's marginal OOS contribution.

## Validation (same discipline as the recal backtest)
Pre-register the metric + thresholds before any code; evaluate **OOS** (walk-forward + blocked K-fold); benchmark against the **market closing total via CLV**; accept the ~11% R² single-game ceiling. **Do not bet totals until a model beats the closing line OOS.** Target: matching even half the market's r = 0.33 would be a genuine product.

## Data-feed readiness audit (2026-06-13) — the Step-1 inputs are already sourced
Verified live in the pipeline today (so "sourcing the feeds" is NOT the next hurdle):
- **Umpire:** `data/news_cache/umpires/officials_<gamePk>.json` (539 cached) + `umpire-refresh.yml`; the diag already carries `ump_k_pct_delta` / `ump_bb_pct_delta`. There is also `data/umpire_assignments.parquet` + `data/umpire_effects.parquet`. ✓
- **Weather:** `tools/weather_runs.py` → `weather_runs_<date>.json` (eff_wind, wind_from, cf_bearing, temp_f, precip_pct, elevation, retractable) via **keyless Open-Meteo** + `data/stadium_coords.json`. The highest-value totals input, already done, $0. ✓
- **Platoon splits:** per-batter `vs_LHP/RHP_OPS_career` + PA in the lineup JSON → aggregate lineup-vs-handedness is computable now. ✓ **One real modeling-data gap:** these are CAREER, not current-season. A current-year vs-hand wOBA is a small Savant-harvest add (`savant_hitters_2026.csv` currently has no per-split columns) — queue it, but it's an upgrade, not a blocker.
- **Park factors:** Savant park-factor harvest + multi-year `stadium_coords.json`. ✓

**So Step 0 (the market total line) remains the ONLY gating hurdle — confirmed empirically:** recent slates carry a market `total_line` on just ~7-8 of 11-13 games (the rest run pred_runs-only), so clean per-game CLV validation requires the free-tier the-odds-api Starter key. The environmental/split inputs are not the blocker; the market line is. This is a POST-FREEZE July job — do not build model code before the freeze lifts.

## Pre-Registered OOS Validation Protocol (Locked 2026-06-13, Rule 2 — signed before any July code)
To prevent premature deployment or overfitting to late-summer environmental shifts, the rebuilt model must pass a strict, multi-layered out-of-sample (OOS) gate before any live capital is deployed. **These thresholds are locked and may not be moved without re-signing.**

### 1. Validation Parameters & Windows
- **Methodology:** Live, blind walk-forward validation against the closing total line (CLV captured at first pitch).
- **Window Duration:** Minimum of six (6) weeks of full MLB slates.
- **Sample Size Floor:** Minimum of **n ≥ 100 graded staked plays** (games where the model's projected total diverges far enough from the closing market line to trigger a simulated bet). If 6 weeks of tracking does not yield 100 staked plays due to thin market-line coverage, the tracking window **automatically extends** until the floor is met. (Counted denominator = staked plays, NOT all slate games — the staked sample grows ~3-4× slower.)

### 2. Dual-Gate Clearing Conditions (BOTH required, simultaneously, on OOS data)
- **Gate A — Calibration baseline:** OOS `pred_runs` must show a statistically significant correlation (r) vs actual total runs that **meets or exceeds the in-sample training baseline**, while maintaining proper dispersion (output SD target ≈ the market's ~1.9-run SD; no regression-to-the-mean collapse).
- **Gate B — Market edge:** Staked plays must generate a **positive aggregate ROI graded against the closing line (CLV)**.

### 3. Capital Rule
- **Strict paper-trading:** the entire validation window is paper-traded. **No live capital** may be allocated during it.
- **Stability hold:** both Gate A and Gate B must hold across **two consecutive weekly reads** before the production switch is flipped — so a single high-variance week cannot trigger a false positive.

> July order of operations: (1) wire the free-tier the-odds-api Starter key, (2) let closing lines pool, (3) run blind walk-forward against THIS protocol. No production totals until both gates hold twice running.
