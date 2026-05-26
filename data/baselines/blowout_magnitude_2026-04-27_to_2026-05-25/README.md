# Blowout-magnitude baseline — 2026-04-27 to 2026-05-25

A frozen snapshot of 28 days of resolved MLB picks, used to test whether
losing by a large margin (|run_diff| ≥ 5) carries information about
signal failure that losing by a small margin does not.

## Why this exists

In late May 2026, while retiring the legacy `recursive_weight_update.
apply_blowout_penalties` path, we considered porting its magnitude
logic — punish features harder when high-conviction picks lose by 5+
runs — into the new symmetric gradient loop in
`mlb_edge/auto_weight_update.py:apply_calibration_from_all_picks`.

The intuitive case for the port: a 12-2 blowout *feels* like a more
serious indictment of our SP / team / bullpen edges than a 3-2 squeaker.

We tested it before shipping. The data invalidated the hypothesis.

## Headline finding

```
Our losses that were blowouts:        31.9%  (44 / 138)
Baseline MLB blowout rate (all games): 30.1%  (116 / 385)
                                       ─────
                                       +1.75pp
```

When we lose, we lose by margins distributed almost exactly the way
MLB games are distributed. If signal failure systematically produced
blowouts, we would expect the rate among our losses to be visibly
higher than the population rate. It isn't. The 1.75pp delta is well
within sampling noise on n=138.

Median pick-probability among blowout losers (0.519) is actually
slightly *lower* than among close losers (0.531) — the opposite of
what the "we were really confident and got crushed" framing predicts.

## What was decided

The magnitude port was **skipped**. The legacy
`apply_blowout_penalties` step was deleted from `auto_weight_update.
run()` (commit follow-up), and `recursive_weight_update.py` was purged
entirely (commit follow-on).

Side benefit: removing the blowout step made the daily ±4% gradient
cap a hard invariant for the first time. Previously the blowout shock
(-15% per qualifying bust) could exceed the cap on slates where a
PLATINUM / DIAMOND bet lost by 5+ runs.

## Two cautions worth flagging at the time of decision

- **PLATINUM blow-loss rate** is 20.0% (6/30) vs GOLD's 12.1% (17/141).
  Could be a real (small) signal about high-conviction picks being
  marginally more sensitive to extreme outcomes; could be noise on
  n=6. Worth checking against this baseline after another 30-60 days
  of data.
- **DIAMOND** has n=1 in this window. Irrelevant for any inference.

## Files in this folder

```
README.md            this file
probe.py             parameterized reproduction script (stdlib only)
picks_resolved.csv   the 299-row pick × outcome join (raw primitive)
summary.json         derived stats (tier table, percentiles, baseline)
```

## How to compare a future window

```bash
python data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/probe.py \
    --start 2026-08-01 \
    --end 2026-08-31 \
    --out data/baselines/blowout_magnitude_2026-08-01_to_2026-08-31/
```

The script depends only on stdlib + MLB Stats API for final scores +
`docs/data/picks_*_diag.csv` files. Output structure matches this
folder exactly, so direct file-by-file diffs are meaningful.

If the new window shows the PLATINUM blow_loss_rate climbing materially
above its baseline here (20.0% on n=30), or the our-losses-vs-MLB-
baseline delta opening up beyond ~5pp, that's the signal to revisit
magnitude weighting in the gradient loop.

## Methodology notes

- "Resolved pick" = a row in `picks_*_diag.csv` whose `pick` column
  matches the home or away team and whose game has a Final status in
  the MLB Stats API.
- "Blowout" = `|home_R - away_R| >= 5`. This threshold was inherited
  from `recursive_weight_update.BLOWOUT_RUN_DIFF` to keep the
  comparison apples-to-apples with the legacy logic.
- Doubleheaders are joined by `game_num` parsed from the matchup
  string `(G1 of 2)` / `(G2 of 2)`. Single games fall back to
  `game_num = 1`.
- SKIP rows are intentionally retained in the CSV (and counted in the
  tier table) so future re-analyses can compare what we bet against
  what we passed on.
