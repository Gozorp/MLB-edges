# Luck-adjusted self-correction probe — 2026-04-27 to 2026-05-26

A frozen snapshot of the Rule-2 pre-flight probe that tested whether
model losses where the picked team won the game-level xwOBA battle
("Bad Beat" / variance) regress to better subsequent 5-game win rates
than losses where the picked team got out-xwOBA'd ("Bad Read" /
flawed logic).

## Why this exists

The proposal was to mute the penalty weight on Bad Beat losses in
`apply_calibration_from_all_picks` to 0.5x, on the theory that the
model shouldn't aggressively unlearn from picks where its underlying
contact-quality read was correct but baseball variance produced a
losing scoreboard.

Per the locked architecture pre-flight (Rule 2: data first, design
second), we backtested whether the Bad Beat cohort actually does
regress to better outcomes before muting any penalty weights.

Locked thresholds (see memory `project_luck_adjusted_probe_thresholds`):

```
X (xwOBA noise gate)     = +0.025
Y (KEEP-mute criterion)  = +8pp  (bad_beat win% - bad_read win%)
KILL threshold           = -3pp
Observation window       = 5 games subsequent
Re-probe trigger         = 2026-06-26 (60-day mark) at +10pp
```

## Headline finding

```
n_joined         = 331 model picks with valid xwOBA join
bad_beat losses  = 33
bad_read losses  = 94
null_zone losses = 47
wins             = 157

bad_beat cohort 5-game window: 80/146 = 54.79%
bad_read cohort 5-game window: 213/431 = 49.42%

delta_pp = +5.37  ->  NULL ZONE
```

**Verdict: NULL — no code change.**

The directional signal is in the predicted direction (Bad Beat losses
*do* regress to a better-than-coin-flip win rate while Bad Read losses
regress almost exactly to random), but the +5.37pp gap does not clear
the locked +8pp KEEP criterion.

## What the result means

- **The hypothesis is not falsified.** Bad Beat cohort win rate
  (54.79%) is meaningfully above the Bad Read cohort (49.42%) and above
  league-average (~50%). This is exactly what the variance-regression
  hypothesis predicts.
- **The signal isn't yet strong enough to act on.** At n=33 Bad Beat
  losses, the 95% CI on the per-game cohort win rate is roughly ±8pp,
  meaning the measured +5.37pp gap could plausibly be noise. The
  locked spec required +8pp to act and that bar wasn't cleared.
- **The Bad Beat 5-game window includes the next-day rematch** in many
  cases (teams play 3-4 game series). If the team that just got
  out-sequenced wakes up the next day still good at hitting baseballs,
  that should show up as immediate regression — and a 54.79% bounce
  rate is consistent with that without proving it.

## What was decided

- **Penalty mute: deferred to re-probe.** No change to
  `apply_calibration_from_all_picks`. The existing stress-mask 0.3x
  and warm-up gate ([[self-learn-safeguards]]) stay as-is.
- **Re-probe locked for 2026-06-26.** At the 60-day mark the joined
  sample roughly doubles. Per the locked spec the re-probe criterion
  to KEEP is tightened to +10pp (vs +8pp on this initial probe) to
  reflect the smaller standard error on the larger sample.
- **No retroactive tuning.** Lowering Y to +5pp on this measured
  result would be data-mining. The spec was locked in advance for
  exactly this reason.

## Why this is a useful negative result

Same value as the lineup_matchup_gap probe baseline: locking the
directional reading in project history means the 60-day re-probe
can do a clean before/after comparison instead of a fresh exploratory
scan. If at 60 days the delta widens past +10pp on the larger sample,
that's a clear KEEP signal we can act on with high confidence. If it
narrows below +5pp, the apparent +5.37pp here was noise and the
hypothesis is dead.

## Files in this folder

```
README.md             this file
probe.py              reusable probe script (stdlib + requests only)
picks_with_xwoba.csv  331-row join of picks × game_xwoba_log + bucket classification
summary.json          frozen verdict snapshot
```

## How to re-run on a future window

```bash
python data/baselines/luck_adjusted_2026-04-27_to_2026-05-26/probe.py
# Or for sanity-check on the window-lookup logic:
python data/baselines/luck_adjusted_2026-04-27_to_2026-05-26/probe.py --dry-run
```

Pre-req: `data/postgame/game_xwoba_log.csv` must be current through the
end of the probe window. Re-run `tools/backfill_game_xwoba.py
--start <last-pulled-date+1>` to extend it.

The probe reads `docs/data/picks_*_diag.csv` (the baked daily slates),
not the root-level pre-bake CSVs.

## When to re-run

Three triggers:

1. **2026-06-26 (locked re-probe at 60-day mark)** — re-run on the
   2026-04-27 → 2026-06-26 window. KEEP criterion is +10pp on the
   larger sample (per locked spec). If clears, mute Bad Beat penalty.
   If null, document directional drift and re-probe again at 90 days.

2. **Anytime `apply_calibration_from_all_picks` semantics materially
   change.** If the penalty-weight loop is refactored, the probe needs
   to validate against the new baseline before re-decisioning.

3. **If model directional hit rate shifts materially** (e.g., dropping
   below 45% or rising above 55% for a sustained period). A different
   model accuracy floor changes the math on which losses to mute.
