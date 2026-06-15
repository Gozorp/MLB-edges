# Dominance / "Ceiling" Engine — Spec & Pre-Registration

**Status:** Phase 1 SHIPPED 2026-06-14 (statsapi-only, display-only). Phase 2 PRE-REGISTERED for July (post-Japan), full Savant pitch-level build.
**Scope guard:** display-only sidecar; NEVER feeds the frozen XGBoost win/totals model. Phase 2 model integration is separately gated (see §4).

This is the CEILING companion to the HR-risk FLOOR engine (`tools/sp_hr_recency.py`,
ERA-gated 5-tier cascade). The floor protects against blow-ups; the ceiling flags
starters who take a game over (the 15-K / 1-hit type outing). They are independent
axes — a pitcher can be high-K AND homer-prone (volatile), or high-floor on both.

---

## Phase 1 — SHIPPED 2026-06-14 (statsapi-only, no new external feed)

Operational decision (user, 2026-06-14): shipping a brand-new nightly Savant CSV
scrape ~9 days before a 2-week unattended international trip is an unacceptable
single-point-of-failure (column/URL/403 risk crashes the chain while away). So
Phase 1 uses ONLY statsapi (an existing dependency) and approximates the bat-missing
signal with rolling-3-start K% (r≈0.85 with CSW%).

`tools/dominance_engine.py` → `docs/data/dominance_<date>.json` (schema `v1-statsapi-proxy`).

**Inputs (per slate SP, statsapi gameLog/season/sabermetrics):**
- `rolling3_k_pct` = ΣSO/ΣBF over the last 3 starts strictly before the slate date (CSW% proxy).
- `season_kbb` = (SO−BB)/BF·100. `k9` = strikeoutsPer9Inn. `xfip` = sabermetrics xFIP (context).
- `opp_k_pct` = opponent team season batting K% (ΣSO/ΣPA); `opp_k_high` if ≥ league_mean + 1.0pp (whiff-prone-lineup proxy for collective chase).

**Flags (2 of 3 buildable now):**
| Flag | Condition | Edge |
|---|---|---|
| ULTRA-DOMINANT | rolling K% ≥ 30 AND opp_k_high | 10+ K / 7+ IP lean; hammer K-prop over; DFS captain |
| HIGH-FLOOR ACE | rolling K% ≥ 28 AND season K-BB% ≥ 20 | quality-start floor (6+ IP, ≤3 ER); low variance |
| HIGH-K LEAN | rolling K% ≥ 28 (neither above) | bat-missing arm; ceiling present, matchup not as soft |

Locked thresholds: ROLL_K_ULTRA 30, ROLL_K_HIGH 28, KBB_FLOOR 20, OPP_K_MARGIN +1.0pp, WINDOW 3 starts.

**Frontend:** green/cyan `▲` chip (`_domChip`) on the game-preview SP row, beside the
red/orange HR-risk chip. Tooltip shows rolling K%, K-BB%, xFIP, opp K%, and the edge.
Fail-safe: missing/failed sidecar → no chip; per-SP try/except; atomic write; never
breaks bake/publish. Wired: chain step 2.98 + publish_local candidate + daily-slate.yml.

**NOT in Phase 1:** the **Matchup Nightmare** flag (kill-pitch whiff × opponent
bottom-5 vs that pitch type) and TRUE CSW% — both need Savant pitch-level data. → Phase 2.

---

## Phase 2 — PRE-REGISTERED for July (post-return), full Savant pitch-level build

**Feed (verified reachable from the box 2026-06-14, HTTP 200 — corrects the stale
"Savant CSV dead" note; it was the player-page/pybaseball exports that 403, NOT the
leaderboard CSVs):**
- `leaderboard/pitch-arsenal-stats?type=pitcher` → per pitch type: `whiff_percent`, `run_value_per_100`, `pitch_usage`, `k_percent`, `est_woba`.
- `leaderboard/pitch-arsenal-stats?type=batter` → per hitter per pitch type (aggregate to the opposing lineup) → opponent run-value/whiff vs a specific pitch type.
- `leaderboard/custom?...&selections=oz_swing_percent,whiff_percent,called_strike_percent,...` → O-Swing% (chase), and a CSW% build (called_strike% + swinging-strike rate).

**Phase 2 flags to add / upgrade:**
1. **True CSW%** (rolling 3-start, needs game-level pitch data) replacing the K% proxy; Dominance Trigger CSW > 32%.
2. **Kill Pitch matchup → MATCHUP NIGHTMARE**: pitcher's highest-whiff pitch with usage > 25% AND opposing lineup ranks bottom-5 vs that exact pitch type → deep game, low run total, high win prob.
3. **Chase multiplier**: pitcher O-Swing% > 35% × opponent collective chase → efficient K's, pitch deep.

**Operational hardening (required before automating the feed):** explicit timeout +
silent fallback on every request; exponential backoff (1→5→15 min) before marking a
download failed; dedicated cache table; Discord alert ONLY on critical failure. Do NOT
add the live feed inside 4 days of any future departure.

**Model-integration gate (separate, never silent):** if a dominance feature is to feed
the totals/HR or K-prop model, pre-register a fresh OOS study (DeLong-significant
log-loss gain + thick |ΔAUC|≤0.01 + sign-correct importances, else NULL), gated
retrain post-freeze. Sits with `phase2_weather_hr/` and the SP-role feature backlog.

---

## Validation (Phase 1, 2026-06-14)
6/15: Chase Burns → High-Floor Ace (L3 K% 34.3, K-BB 21.9, xFIP 3.2). 6/13: Skubal
(31.0/22.5/2.76) + deGrom (29.9/24.3/3.23) → High-Floor Ace; Lake Bachar → Ultra
(33.3 + whiff-prone opp); Liberatore high-K but wild (K-BB 12.4) → correctly only
High-K Lean, not Ace. Floor/ceiling independence confirmed (Liberatore = EXTREME HR
floor + High-K ceiling on the same start).
