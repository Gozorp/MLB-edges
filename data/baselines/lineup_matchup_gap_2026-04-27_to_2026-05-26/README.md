# Lineup-matchup-gap probe — 2026-04-27 to 2026-05-26

A frozen snapshot of the Rule-2 pre-flight probe that gated the proposed
F6 "Lineup Dominance" conviction signal.

## Why this exists

On 2026-05-27 we considered adding F6 to `edge_calculator.py` — a new
conviction signal that would fire when one team's lineup had a strongly
positive matchup against the opposing starting pitcher. The proposal
included:

1. Compute `lineup_matchup_gap = home_lineup_score - away_lineup_score`
2. Wire as a new XGBoost feature with `monotone_constraint=+1`
3. Add a 6th conviction signal F6 that can bump SKIP→GOLD or PLAT→DIAMOND
4. Add the gap to `weights_state.json` for symmetric gradient scaling

Per the locked architecture pre-flight (Rule 2: data first, design second),
we backtested the predictive power of the available proxy
`(home_lineup_concentration - away_lineup_concentration)` against actual
MLB outcomes before building anything.

## Headline finding

```
n_pairs            = 184
win_rate           = 0.543
AUC                = 0.4864    <- BELOW 0.50, way below the 0.52 kill threshold
Pearson r          = -0.0189   <- essentially zero
gap distribution   = mean +0.046  stdev 0.17
                     p10=-0.19  p50=+0.06  p90=+0.27
```

**Verdict: RED — F6 killed.**

The signal is not just weak — it's slightly *anti-correlated* with winning.
An AUC below 0.50 means picks with a higher concentration gap won at a
marginally lower rate.

## Why anti-correlated — root cause

Investigation of `mlb_edge/lineup_shape.py:concentration_index` confirmed
that `lineup_concentration` measures **top-heaviness** (ratio of top-3 vs
bottom-3 xwOBA), NOT lineup-vs-SP matchup strength. The module's own
docstring warns:

> "Top-heavy lineups are more vulnerable to:
>   * losing a star to injury / pinch-hit / late-inning sub
>   * weak innings starting from the 6-7-8 hole
>   * relief pitchers who navigate the top of the order successfully"

So the slight anti-correlation we measured is *expected behavior* of the
underlying feature — top-heavy lineups are structurally exploitable by
competent pitching. A real lineup-matchup feature (per-batter xwOBA vs
this specific SP, lineup-spot-weighted) wasn't even being computed.

## What was decided

- **F6 conviction signal: killed.** Not built. Conviction layer stays F1/F2/F3/F5.
- **Dashboard `Lineup Edge` card: filed for label correction** (task #159).
  Currently displays `lineup_concentration` as if it were "lineup quality"
  — should rename or add tooltip clarifying it's top-heaviness.
- **Bottom-up sprint Phase 2-7: filed to resume** (task #160). The actual
  Log5 per-batter-vs-SP matchup engine is what Phase 2-7 was supposed to
  deliver. Phase 1 only shipped the shape index. Re-running this probe
  against the real matchup gap (post-Phase 2-7) is the right next step
  before reopening the F6 question.
- **XGBoost feature add: deferred** (task #161). Adding `lineup_concentration`
  as a raw XGBoost feature with `monotone_constraint = -1` (top-heaviness
  hurts win prob, matching the data) is defensible but requires a retrain.
  Not urgent.

## Files in this folder

```
README.md          this file
probe.py           parameterized reproduction script (stdlib only)
picks_with_gap.csv 184-row pick × outcome join with pick-side oriented gap
summary.json       derived stats + verdict
```

## How to compare a future window

```bash
python data/baselines/lineup_matchup_gap_2026-04-27_to_2026-05-26/probe.py \
    --start 2026-08-01 \
    --end 2026-08-31 \
    --out data/baselines/lineup_matchup_gap_2026-08-01_to_2026-08-31/
```

The probe uses only stdlib + MLB Stats API + `docs/data/picks_*_diag.csv`
files. Output structure matches this folder so direct file-by-file diffs
are meaningful.

## When to re-run

Two triggers should prompt a re-probe:

1. **Phase 2-7 of the bottom-up sprint plan ships** — at that point a real
   `lineup_matchup_gap` column exists in the diag CSV (Log5 per-batter
   roll-up). Re-run with that column instead of the concentration proxy.
   If AUC clears 0.55, F6 becomes justified.

2. **The NaN rate on `lineup_concentration` drops materially** — today
   ~50% of rows skip because lineups weren't posted at slate-run time.
   If we cache last-known-good lineups and that rate falls to <20%, the
   probe sample size doubles and AUC stability improves.
