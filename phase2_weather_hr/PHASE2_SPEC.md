# Phase 2 — Weather x Savant HR model integration (SHADOW BRANCH)

> **Status: design + skeletons only. NOT wired into production. Execute in JULY,
> after the SFO->Japan freeze lifts. Deploy to `main` ONLY if the locked
> acceptance gates pass.** See memory `project_weather_hr_phase2_spec`.

## Why this is a retrain, not a tweak
The XGBoost already carries weather columns (`temp_f`, `wind_dir_park`,
`humidity_pct`, `precip_prob`; `feature_engineering.weather_carry_adjustment`)
plus the Savant inputs (barrel%, EV, LA). So Phase 2 = **validate + re-weight via
a retrain**, not a from-scratch add. XGBoost has no hand-tunable linear weights —
it learns splits, so "optimizing the weights" = retrain + OOS validation. The
daily self-learn loop only does a bounded gradient on the **existing** signal
weights; it cannot create a new weather feature. The initial retrain establishes
the weather-interaction features; self-learn then maintains them.

## Feature-engineering thesis (the substance)
Weather acts on **balls in the air**, not all contact. A barrel into a 15 mph
outwind carries far; the same wind does ~nothing to a grounder. So weather enters
as **interactions with batted-ball quality, not additively**:

- `effective_wind` = park-projected, dampened, altitude-adjusted SIGNED wind
  (the `weather_runs.py` formula: FROM->TO flip, `wind_coef`, alt bump,
  `cos(dtheta)`). Replaces raw `wind_dir_park` degrees.
- `air_density_index` = f(temp, elevation_ft, humidity) carry multiplier
  (warm / high / humid = thinner air = more carry; Coors 5200 ft outlier).
- **Interaction features (the core additions):**
  - `effective_wind * barrel_rate`   (and `* hard_hit%`, `* fly_ball%`)
  - `air_density_index * avg_launch_angle`   (and `* fly_ball%`)
  - `park_hr_factor * effective_wind`
- Training uses the ACTUAL past weather (Open-Meteo **archive** API), not the
  pre-game forecast `weather_runs.py` uses for display.

## Ingestion (build_hr_training_labels.py)
Per historical game this season -> one row per team-game:
- actual first-pitch weather from the Open-Meteo **archive** endpoint (lat/lon +
  date + hour),
- lineup Savant HR-quality (barrel%/EV/LA/xwOBAcon/FB%) computed
  **season-to-date BEFORE the game** (no leakage),
- park `cf_bearing` / `wind_coef` / `elevation_ft` (stadium_coords.json),
- the engineered + interaction features,
- LABEL = actual HRs hit (statsapi boxscore), total + per-team.
All three sources are historical -> the whole season is **backfillable in July**;
no daily logging needed during the freeze.

## Retrain + LOCKED acceptance gates (retrain_hr_weather.py)
Train XGBoost with existing + weather-interaction features; validate OOS
(dual walk-forward + K-fold, burn-in >= 60 obs). Mirrors
`project_totals_recal_prereg` / `project_empbayes_sp_xera_prereg`.

PASS requires ALL of:
1. OOS log-loss / Brier on the HR (and/or total-runs) target improves vs the
   incumbent no-weather model, DeLong/bootstrap-significant.
2. Thick-subset `|dAUC| <= 0.01` (no degradation on the bulk).
3. Weather-feature importances non-trivial AND **sign-correct**
   (wind-out = +HR, wind-in = -HR, thin air = +).
-> else **NULL**: do not add; keep the display-only `weather_runs` badge; stay frozen.
Simplest model wins. No moving gates without re-sign.

## July execution order
backfill labels -> retrain -> validate vs gates -> if PASS: merge to `main`,
one manual retrain to set the baseline, add Open-Meteo-archive to the cloud
feature build, let self-learn resume on the new feature set, re-freeze. If NULL,
close it out and keep the Phase-1 display badge.
