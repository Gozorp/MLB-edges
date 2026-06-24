# GOLD-Tier Performance Tripwire — Pre-Registration (LOCKED 2026-06-24)

**Status:** Pre-registered under Rule 2. All parameters below are fixed BEFORE the code runs
and may not be tuned to taste afterward. **Read-only monitor — freeze-safe; touches no model
weights, calibration, picks, tiers, or stakes.** Purpose: during the SFO→Japan travel freeze
(model frozen 2026-06-03 → 07-20), alert if the GOLD tier's *realized* win rate degrades far
enough that staleness — not variance — is the likely cause.

## Why this exists
The health system alerts on infrastructure (did it publish/deploy, is data complete) but has
**no alert on realized performance**. A frozen model's real risk is staleness (rosters/bullpens
drift from the June-13 snapshot). This watches for that, sample-gated so daily variance can't
fire it. See `MODEL_7DAY_DIAGNOSIS_2026-06-24.md`.

## Locked parameters
- **Baseline p0 = 0.57** (historical GOLD win rate; the tier's measured edge).
- **Window = trailing 30 calendar days** of GOLD picks, anchored at the latest scored slate date.
- **Eligibility = confirmed-final, non-void GOLD picks only** (rows with `pick_correct` set in
  `oos_ledger.jsonl`; tier resolved from the matching `picks_<date>_diag.csv`). Not-yet-final
  games are simply absent — the ledger's 1-2 day scoring lag therefore cannot trip it.
- **Sample gate: n >= 45 GOLD picks** in the window. Below 45, status = `INSUFFICIENT` (never
  alerts) — percentage variance is too volatile to trust on thin volume.
- **Floors (binomial SE, recomputed from the live n each run):**
  `sigma = sqrt(p0*(1-p0)/n)`
  - **YELLOW (warning) = win% < p0 - 1.5*sigma** (~47-48% at n=60-90)
  - **RED (critical) = win% < p0 - 2.0*sigma** (~44-46% at n=60-90)
  - else **GREEN**.
  Self-adjusting: more picks -> smaller sigma -> floor closer to baseline (more sensitive), which
  is the statistically correct behavior.
- **Cadence: daily**, in the cloud (GitHub Actions) so it runs with the laptop off.
- **Alert channel:** existing `DISCORD_HEALTH_WEBHOOK`. Rate-limit: same level no more than once
  per 24h (state in `docs/data/gold_tripwire_state.json`).
- **Snapshot:** writes `docs/data/gold_tripwire.json` every run (n, win%, floors, status) for the
  dashboard / audit trail, regardless of alert.

## Interpretation (what an alert means — and does NOT mean)
- **YELLOW:** GOLD has slipped into a historically rare stretch. *No action* — keep monitoring.
- **RED:** ~97.7% probability the slump is not pure variance; staleness is the leading suspect.
  Candidate responses (the operator's call, not automated): reduce unit sizing, or schedule a
  manual weights refresh. **The tripwire never changes the model itself.**
- A RED is a *prompt to look*, not proof of breakage. The frozen-model + variance findings still
  apply; this only flags when the deviation exceeds the 2-sigma noise floor on adequate volume.

## Explicitly out of scope (no scope creep)
- No auto-intervention, no stake changes, no weight edits, no calibration changes.
- No change to `health_check.py` (isolated to avoid destabilizing the critical monitor in freeze).
- Thresholds/window/gate are frozen by this document.
