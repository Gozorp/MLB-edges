# game_xwoba_log.csv — schema

Per-game team-aggregate xwOBA log. Input for the luck-adjusted
self-correction probe (task #168, see project memory
`project_luck_adjusted_probe_thresholds.md` for the locked thresholds).

## Locked 2026-05-27 — methodology spec

**Numerator: `estimated_woba_using_speedangle`** (NOT `woba_value`).

Statcast populates this column with:
- Expected wOBA based on launch angle and exit velocity, for batted balls.
- Standard wOBA event weights for non-batted-ball terminal events
  (walks, strikeouts, hit-by-pitches).

Summing `woba_value` would yield actual wOBA, which correlates ~1.0
with actual runs and would collapse the Bad Beat bucket to near-empty.
The whole probe depends on the gap between expected contact quality
and actual outcomes — using `woba_value` erases that gap by
construction.

**Denominator: `woba_denom`** — natively excludes intentional walks,
sacrifices, and other non-counted plate appearances.

**Formula:**
```
team_game_xwoba = sum(estimated_woba_using_speedangle WHERE woba_denom > 0)
                  / sum(woba_denom)
```

Filter to terminal pitches only (one row per PA outcome). The
`woba_denom > 0` filter is the canonical PA selector — non-terminal
pitches in the Statcast CSV have woba_denom = NaN / 0.

## CSV columns

```
game_pk            int    Canonical join key (MLB Stats API gamePk).
                          Use this — NOT away@home@date — to avoid
                          doubleheader collisions. See [[dh-results-keying]].
game_date          str    ISO date YYYY-MM-DD.
home_team          str    3-letter abbrev matching docs/data/picks_*_diag.csv.
                          CHW not CWS, OAK not ATH (per H2H abbrev map fix).
away_team          str    Same convention.
home_xwoba         float  Computed per the locked formula above.
away_xwoba         float  Same.
home_score         int    Final runs scored by home team.
away_score         int    Final runs scored by away team.
n_pa_home          int    Plate appearances counted (rows where
                          woba_denom > 0) for the home team.
                          Thin-sample guard: rows with n_pa < 25
                          should be flagged in the probe analysis.
n_pa_away          int    Same for away team.
source_pulled_at   str    ISO UTC timestamp of the Statcast CSV pull
                          that produced this row. For reproducibility
                          / staleness audits.
```

## Source endpoint

`https://baseballsavant.mlb.com/statcast_search/csv`

Query params: date range (`game_date_gt`, `game_date_lt`), `season`,
`group_by=name`, plus the standard "all" filters.

Pull date-by-date to keep the CSV under the endpoint's row cap (~25k
rows). One day = ~3000 pitches × ~13 games ≈ 230 rows per game on
the pitch-level dump, so a daily pull is well under the cap.

Use stdlib `requests` only — no `pybaseball` dependency, matching the
existing Savant CSV harvest pattern ([[always-harvest-baseball-savant-csvs]]).

## Window

Backfill from 2026-04-27 (earliest pick_*_diag.csv) through the
day before today. Future incremental pulls append one row per
game played that day.

## Validation gates (probe.py must enforce)

1. Row count matches MLB Stats API game count for the date range
   (modulo postponed/cancelled — should match `officialDate` games).
2. `home_xwoba` and `away_xwoba` both in [0.100, 0.600] — outside
   this range is almost certainly a parsing bug, not a real signal.
   Empirical update 2026-05-27: 11/400 games on the initial backfill
   landed outside the original [0.15, 0.50] band — all of them legit
   blowouts or shutouts (e.g. TOR shut out at 0.102 xwOBA, NYM contact
   night at 0.571). Widened to [0.10, 0.60] to avoid false alarms.
3. `n_pa_home >= 25 AND n_pa_away >= 25` for the row to count toward
   the probe sample. Thin-sample games (rain-shortened, 5-inning
   doubleheader G2 in older eras) get dropped from the bucket math.

## Joins

Join `picks_*_diag.csv` to `game_xwoba_log.csv` on `game_pk`.

Currently `picks_*_diag.csv` doesn't expose `game_pk` as a column —
it's keyed by `matchup` string. The backfill script (#167) must
either (a) recover game_pk by re-querying MLB Stats API by
(game_date, home_abbr, away_abbr), or (b) the model-side bake step
gets updated to emit `game_pk` into the diag CSV. (b) is cleaner
long-term; (a) is fine for the one-time backfill.
