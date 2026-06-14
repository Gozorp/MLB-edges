# Within-Appearance Bullpen Fatigue — Shadow Feature Spec

**Status:** `experimental` · `offline-only` · `shadow`. Built 2026-06-12.
**Script:** `tools/bp_fatigue_features.py`
**Sink:** `data/shadow/bp_fatigue/` (isolated; never `docs/data` or any published path)

This is **not** the existing "Bullpen fatigue (7-day window)" sidecar. That one is a
*between*-appearance rest tracker (rest days / pitches-over-72h / consecutive days) keyed
off the schedule API and rendered in `bullpen_meta`. **This** is a different data grain
entirely: pitch-by-pitch *within* a single appearance, detecting the onset of rapid muscle
fatigue as a reliever's stuff degrades pitch to pitch.

## Freeze / pre-registration posture
Decoupled shadow pipeline. Imports nothing from `mlb_edge/`, modifies no production module,
is **not** wired into `predict.py`, the frozen booster, or the nightly chain. It reads raw
Statcast pitch events and writes a tagged per-pitch dataset to an isolated sink. Promoting
any of these features to the live inference payload is a **July pre-registered study**
(offline-lift gate: feature-level AUC / log-loss improvement on a walk-forward split must
clear the standing bar) — a config flip once the freeze lifts, not this deliverable.

## Inputs / grouping
Canonical Statcast pitch-by-pitch columns (`release_speed`, `release_pos_x/z`,
`release_spin_rate`, `pfx_z`, `zone`, `pitch_type`, `events`, `description`, …). Grouped by
**(game_pk, pitcher)** = one appearance (resets all stateful counters), with `(pitcher,
game_date)` exposed per the spec. Relievers = appearances whose first inning ≥ 2 (didn't
start the game); `--include-starters` overrides. Non-pitch rows (pickoffs, mound visits,
glitched/no-radar rows) are dropped so state never advances on a non-pitch.

## Feature set (the four compounding factors)
1. **Pitch-count tiers** — rolling `app_pitch_num`; booleans `flag_15` (onset), `flag_20`
   (sharp drop), `flag_30` (critical), plus categorical `pitch_tier` and `ab_pitch_num`.
   The spec's explicit windows (pitches 1-10 baseline vs 15+/20+/30+) drive every baseline.
2. **Velocity degradation** — **primary fastball only** (the appearance's most-thrown FB
   subtype; mixing FF/SI/FC inflates "drops" with pitch-type velo gaps). Radar gated to
   70–106 mph (drops glitches / position-player lobs). `velo_drop_vs_app` (vs pitches 1-10)
   and `velo_drop_vs_inning` (leak-safe expanding per-inning baseline), winsorized ±8;
   `flag_velo_drop` fires > **1.5 mph** below the inning baseline (1.0–1.5 band configurable).
3. **Control / command** — `relx_roll_std` / `relz_roll_std` (release-point variance),
   `armslot_drop` (vertical-release decay vs early baseline → `flag_armslot_drop`),
   `in_zone` + `zone_pct_roll` + `zone_drop` vs the 1-10 baseline → `flag_zone_drop`.
4. **Spin & movement** — on breaking balls: `spin_drop` (rpm) and `ivb_drop` (induced
   vertical break, inches) vs the early breaking-ball baseline → `flag_spin_loss`,
   `flag_break_loss` (the "lost bite" signal).

**Fatigue Index (0–100):** transparent weighted blend —
`30%` count · `30%` velo · `22%` command · `18%` stuff (weights in `CONFIG`, tunable under
the July study). **`out_of_breath`** is the compounding boolean: past the onset tier
(`flag_15`) **AND** ≥ 2 of the three degradation systems (velo / command / stuff) firing.

## Target (for XGBoost)
`neg_outcome_pitch` (this pitch's immediate event) and `ab_neg_outcome` (the at-bat ended in
a **walk / hit / wild pitch**) — the latter is the cleaner per-pitch training label for
`P(negative outcome | accumulating fatigue)`.

## Validation (built into the build)
`python tools/bp_fatigue_features.py --selftest` — 5 deterministic checks: clean-inning
counter increment, cross-appearance state reset, pickoff/glitch rows dropped without
advancing the count, a 32-pitch fatigue ramp (15/20/30 flags + late velo-drop +
`out_of_breath` + Fatigue Index 0→76), and the schema-contract + data-quality bounds.

`--demo` on the local Statcast cache (real 2023 relievers, 2,670 appearances / 50,635
pitches) — **Fatigue Index rises monotonically by tier** and the negative-outcome rate
shows its clearest lift at the critical tier:

| tier  | n     | neg-outcome | mean FI | out_of_breath |
|-------|-------|-------------|---------|---------------|
| 1-10  | 25929 | 31.1%       | 5.4     | 0.0%          |
| 11-14 | 8048  | 32.0%       | 10.2    | 0.0%          |
| 15-19 | 6766  | 29.9%       | 17.6    | 5.1%          |
| 20-29 | 6149  | 31.1%       | 28.0    | 5.3%          |
| 30+   | 3743  | 35.6%       | 38.1    | 6.1%          |

**Survivorship caveat (why the multivariate model matters):** the middle tiers are flat
because managers leave pitchers past 15 *selectively* — the ones cruising. That's precisely
why the degradation features (velo / command / spin drops) earn their keep over raw pitch
count: at the same tier they separate the genuinely-gassed from the comfortable veteran.
The 30+ jump is the unconditional critical-fatigue signal.

## Run
```
python tools/bp_fatigue_features.py --selftest
python tools/bp_fatigue_features.py --demo
python tools/bp_fatigue_features.py --input "data/statcast_cache/statcast_chunk/*.parquet" --out data/shadow/bp_fatigue/tagged_pitches.parquet
```
Output schema is contract-enforced (`SCHEMA` in the script); atomic writes; thresholds in
`CONFIG` are **user-specified, not invented** — tune only under the July pre-registration.
