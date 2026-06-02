# SP Primary-Role Categorical Feature — Post-Trip Implementation Plan

**Status:** DEFERRED to post-Japan (decided 2026-06-01, user choice "Full XGBoost retrain, post-trip").
Do **not** start before the SFO freeze lifts — this is a core-model retrain.

**One-line goal:** add a categorical `sp_role` feature that distinguishes full-time
starters from openers / spot-starters / bulk relievers, retrain the XGBoost on it,
and keep it only if a locked-threshold backtest shows it beats the current model.

---

## 0. Why this is a *probe*, not a given
The model **already** carries `sp_ip_per_start` (= IP ÷ games-started) in
`SP_FEATURE_COLS` (`mlb_edge/feature_engineering.py:43`). That continuous feature
already encodes most of the opener signal (opener ≈ 1 IP/start, starter ≈ 5–6).
So the categorical must prove **marginal** lift over a baseline that already sees
IP/start — most likely on the opener/spot subset, not the whole slate. Treat the
expected effect as small and let the backtest decide.

## 1. Role taxonomy (proposed — LOCK exact cutoffs at kickoff, Rule 2)
Classify each game's starter from **season** statsapi pitching stats:
`gamesStarted (GS)`, `gamesPlayed (G)`, `inningsPitched (IP)`; derive
`ip_per_start = IP_in_starts / GS` and `start_share = GS / G`.

| Role | Rough rule (lock before coding) |
|---|---|
| `TRADITIONAL` | `start_share >= 0.8` AND `ip_per_start >= 4.0` |
| `OPENER` | `GS >= 3` AND `ip_per_start <= 2.0` |
| `BULK` | low GS, high relief IP, frequently the bulk arm behind an opener |
| `SPOT` | `start_share <= 0.3` (mostly reliever) making an occasional start, OR `GS <= 2` this season |
| `UNKNOWN` | thin data (ties into the existing 100-Statcast-pitch gate; do not force a label) |

Encode as one-hot (drop `UNKNOWN` → all-zero, which the model already tolerates as missing).

## 2. Data sources (already in the pipeline — no new fetch)
- `mlb_edge/pitching_quality.py:351` — `stats=season&group=pitching` already pulls
  `gamesPlayed`, `gamesStarted`, `inningsPitched`.
- `mlb_edge/fallback_stats.py:140,150` — `inningsPitched`, `gamesStarted`, and an
  `sp_ip_per_start` derivation already exist.
- `mlb_edge/main_predict.py:581` — `_games_started_map(slate_date)` already exists.
Add a small `classify_sp_role(season_row) -> str` helper next to these.

## 3. Implementation steps (post-trip)
1. Add `classify_sp_role()` + unit tests (fixtures for each role, incl. edge cases).
2. Add `sp_role` (one-hot cols) to `SP_FEATURE_COLS` / the per-game feature builder.
3. **Recompute historically:** backfill `sp_role` across the full training window so
   the retrain has it on every past game (the hard part — season role at each
   historical date, not today's role).
4. **Retrain** the XGBoost main model with the new columns.
5. **Re-calibrate** (the calibrator on top of `p_model`) and refresh
   `refit_calibrator`.
6. Wire into the live feature row in `build_pipeline.py` (alongside `sp_*_gap`).

## 4. Locked-threshold backtest (Rule 2 — lock BEFORE any code)
- **Baseline:** current production model (which already has `sp_ip_per_start`).
- **Metric:** out-of-sample Brier / log-loss, computed on held-out historical slates,
  **and separately on the opener+spot subset** (where the feature should matter).
- **Proposed keep/kill (confirm + lock at kickoff):**
  - KEEP if OOS Brier on the opener+spot subset improves by `>= X` (lock X, e.g. 0.5pp)
    AND whole-slate Brier does **not** regress beyond noise.
  - KILL if no subset improvement, or any whole-slate regression.
  - Min sample: `n >= [lock]` opener/spot games across the backtest window.
- No retroactive tuning of thresholds after seeing results (per `project_override_backtest_thresholds`).

## 5. Risks / notes
- Redundancy with `sp_ip_per_start` → expect small lift; the subset eval is the real test.
- Historical role backfill is the main effort + bug surface (point-in-time correctness).
- Retrain + recalibration is the freeze-sensitive part — only run with full backtest + no travel pressure.
- A free, freeze-safe down payment available any time: bake `sp_role` as a **display
  chip + diag column** (no model change) so it's visible and the historical data starts
  accumulating cleanly for the eventual retrain. (User deferred this too; offer again post-trip.)
