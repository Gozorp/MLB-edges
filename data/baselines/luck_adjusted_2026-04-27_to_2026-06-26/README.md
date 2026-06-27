# Luck-adjusted self-correction probe — 60-day re-probe (2026-04-27 to 2026-06-25)

A frozen snapshot of the **60-day re-probe** (run 2026-06-26) of the
Rule-2 pre-flight test for whether model losses where the picked team
won the game-level xwOBA battle ("Bad Beat" / variance) regress to
better subsequent 5-game win rates than losses where the picked team
got out-xwOBA'd ("Bad Read" / flawed logic).

This is the locked follow-up to the initial 30-day probe at
`data/baselines/luck_adjusted_2026-04-27_to_2026-05-26/`. Same
methodology, doubled sample, **tighter +10pp KEEP criterion** per the
locked spec.

## Why this exists

The proposal was to mute the penalty weight on Bad Beat losses in
`apply_calibration_from_all_picks` to 0.5x, on the theory that the
model shouldn't aggressively unlearn from picks where its underlying
contact-quality read was correct but baseball variance produced a
losing scoreboard.

Per the locked architecture pre-flight (Rule 2: data first, design
second), we re-backtest whether the Bad Beat cohort actually does
regress to better outcomes before muting any penalty weights. The
intervention stays gated until the data clears the bar.

## Locked thresholds for THIS probe

See memory `project_luck_adjusted_probe_thresholds`. The re-probe uses
the **tighter** Y per the lock (smaller standard error on the larger
sample); X, KILL, and the window are unchanged:

```
X (xwOBA noise gate)     = +0.025
Y (KEEP-mute criterion)  = +10pp   (re-probe; was +8pp on the 30-day probe)
KILL threshold           = -3pp
Observation window       = 5 games subsequent
Null zone                = [-3pp, +10pp]
```

## Headline finding

```
n_joined         = 659 model picks with valid xwOBA join
bad_beat losses  = 64
bad_read losses  = 190
null_zone losses = 85
wins             = 320

bad_beat cohort 5-game window: 169/296 = 57.09%
bad_read cohort 5-game window: 449/900 = 49.89%

delta_pp = +7.21  ->  NULL ZONE
```

**Verdict: NULL — no code change.**

The directional signal is in the predicted direction and has
**strengthened** versus the 30-day probe (Bad Beat losses regress to a
clearly better-than-coin-flip win rate; Bad Read losses regress almost
exactly to random), but the +7.21pp gap does not clear the locked
+10pp KEEP criterion.

## How this compares to the 30-day probe

```
                       30-day (05-26)     60-day (06-25)
joined sample          331                659
bad_beat losses        33                 64
bad_read losses        94                 190
bad_beat win%          54.79%             57.09%
bad_read win%          49.42%             49.89%
delta_pp               +5.37              +7.21
KEEP criterion         +8pp               +10pp
verdict                NULL               NULL
```

The delta **widened** from +5.37pp to +7.21pp as the sample doubled —
the hypothesis is moving in the right direction, not decaying toward
zero. But the KEEP bar also tightened (+8pp → +10pp by design), and the
measured gap still sits inside the null zone. The Bad Read cohort
remains pinned at ~50% (textbook regression-to-random for losses with
no contact-quality alibi), while the Bad Beat cohort sits ~7pp above
it. Encouraging, not yet actionable.

## What the result means

- **The hypothesis is not falsified — and is now better-supported.**
  Bad Beat cohort win rate (57.09%) is meaningfully above the Bad Read
  cohort (49.89%) and above league-average (~50%). This is exactly what
  the variance-regression hypothesis predicts, and the gap grew with
  sample size rather than shrinking.
- **The signal still isn't strong enough to act on under the locked
  bar.** At n=64 Bad Beat losses the per-game cohort win rate still
  carries a wide CI, and the +10pp re-probe criterion was set precisely
  to avoid acting on a gap this size. +7.21pp < +10pp → hold.
- **No retroactive tuning.** Lowering Y to +7pp to clear this measured
  result would be data-mining. The +10pp bar was locked in advance for
  exactly this reason.

## What was decided

- **Penalty mute: deferred again.** No change to
  `apply_calibration_from_all_picks`. The existing stress-mask 0.3x and
  warm-up gate (`self-learn-safeguards`) stay as-is. The mute remains
  gated on an explicit user sign-off that cannot happen until the data
  clears +10pp.
- **Third re-probe scheduled for 2026-07-26 (90-day mark).** Same
  locked spec, same +10pp KEEP criterion, same 5-game window. At ~90
  days the joined sample grows by roughly another third; if the delta
  holds or widens past +10pp there, that is a clean KEEP signal to act
  on. If it narrows back below +5pp, the apparent strengthening here
  was noise.

## Methodology note (join key)

The probe keys the xwOBA log by `(game_date, away_team, home_team)`,
not by `game_pk`. The merged `game_xwoba_log.csv` holds 787 game_pk
rows across the 60-day window; these collapse to 782 unique
date+teams keys (≈5 doubleheaders where both games share a key — last
one wins). This matches the 30-day probe's behaviour exactly, so the
60-day result is directly comparable. A game_pk-keyed join is a
candidate refinement for the 90-day re-probe if doubleheader volume
grows, but it was deliberately left unchanged here to keep the
before/after comparison clean.

## Files in this folder

```
README.md             this file
probe.py              probe script (Y_KEEP_DELTA_PP=10.0; clean rebuild —
                      the 05-26 copy had a corrupted duplicated tail)
picks_with_xwoba.csv  659-row join of picks x game_xwoba_log + bucket class
summary.json          frozen verdict snapshot
```

## How to re-run on a future window

```bash
# 1. Extend the xwOBA archive (writes a SEPARATE file, then merge):
python tools/backfill_game_xwoba.py --start <last+1> --end <new-end> \
       --out data/postgame/_xwoba_incr.csv
#    (the script OVERWRITES its --out target; never point it at
#     game_xwoba_log.csv directly or you clobber the early archive.)
# 2. Merge incr into data/postgame/game_xwoba_log.csv (dedupe by game_pk).
# 3. Run the probe:
python data/baselines/luck_adjusted_2026-04-27_to_2026-06-26/probe.py
```

## When to re-run

1. **2026-07-26 (locked 90-day re-probe)** — re-run on the
   2026-04-27 → 2026-07-25 window. KEEP criterion stays +10pp. If it
   clears, route to user for sign-off on the 0.5x Bad Beat penalty
   mute. If null, document and decide whether a 4th re-probe is worth
   it. If it drops below KILL (-3pp), shelve the hypothesis.
2. **Anytime `apply_calibration_from_all_picks` semantics materially
   change** — the probe must re-validate against the new baseline.
3. **If model directional hit rate shifts materially** (sustained
   below 45% or above 55%) — the loss-muting math changes.
