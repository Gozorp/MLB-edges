# Player-Driven Ecosystem Ratings — Spec & Pre-Registration

**Status:** Track 1 (shadow overlay) SHIPPED 2026-06-17, display/shadow-only. Track 2 (Model-B retrain) PRE-REGISTERED for the July sandbox. Production XGBoost stays FROZEN through June.
**Origin of design:** user spec (5-day player layers, K×(W−E) penalty, `Team_Ecosystem_Score` + `Interval_Delta`), motivated by the tier report's two findings — (a) the frozen model is geometrically **compressed** (recovers <½ the true tier spread), and (b) the **ace-on-a-bad-team** distortion.

---

## Track 1 — Shadow overlay (LIVE NOW, zero model risk)

`tools/rating_shadow.py` → `docs/data/rating_shadow_<date>.json`. Wired silent into the nightly chain (step 2.99) + publish_local + daily-slate.yml. **Never** feeds the model, picks, or stake.

**0–100 layers (statsapi-only; no new external feed before the trip):**
- **Batter** — per hitter from OPS (+, 0.75w) and K% (−, 0.25w), league-normalized via normal-CDF → 0–100. Team batter rating = mean of top-9-by-PA. *(Hard-Hit% = July add via Savant; lineup-specific average once lineups post.)*
- **SP** — per pitcher from K-BB% (+, 0.5), WHIP (−, 0.3), HR/9 (−, 0.2). The game uses the diag's projected starter; TBD → staff mean.
- **BP** — team relief execution (K-BB%, WHIP) blended with **current fatigue** from `bullpen_meta` (rest/strain tier: STRAINED −12 … FRESH +5).
- **Team Ecosystem Score** = 0.45·batter + 0.35·SP_today + 0.20·BP. Solves ace-on-bad-team by construction (a 90-SP on a 50-bat roster reads as a strong arm in a weak ecosystem, not a strong team).

**Interval_Delta** = `clamp(2.5·(W − E), −7, +7)` over the trailing 5 days, W = actual wins, E = Σ frozen-model pre-game win prob for those games. Expectation-relative: an elite team that was *supposed* to go 4–1 and went 1–4 drops ~7.5→cap 7; a poor team that goes 1–4 as expected moves 0. Stored as a **separate** field from the level score (so a future tree can split on "high level + sharply negative delta = tailspin").

**Shadow decompression (logged, NOT applied):** `adj_logit = logit(raw_home_prob) + 0.35·z(eco_diff)`; `confidence_delta_pp = 100·(adj − raw)`. GAMMA=0.35 is an **untuned v0** — it is *evaluated*, never tuned on the same data, and never applied to a pick. First live read (6/17): 8 games moved ≥4pp (e.g., TB@LAD 0.64→0.79; NYM@CIN 0.50→0.31).

---

## Track 2 — July Model-B sandbox (PRE-REGISTERED, retrain gated)

Do **not** retrain production. In July, on a clone:

1. **Historical feature ledger** (`tools/build_rating_ledger.py`) — recompute `Team_Ecosystem_Score` + `Interval_Delta` **as-of each historical game date** (no leakage: stats through date−1, interval from the prior 5 days), joined to the frozen `home_prob`, the actual outcome, and the team tier. Live shadow uses *player-level* current-season ratings; the full-season historical ledger uses *team-level* `byDateRange` as-of stats (player-level historical = per-player byDateRange, a July compute decision documented here, not silently swapped).
2. **Isolation test** — train Model B (frozen-model features **+** the 2 new columns) and run the **exact** `SP_FEATURE_BACKTEST_PREREG.md` OOS protocol (walk-forward, bootstrap CI, sign check, tail check). Feed the 2 columns as tree splits — do **not** add the heuristic shadow prob itself (avoids the heuristic feedback loop).

**Primary pre-registered hypothesis (locked):** *the ecosystem features reduce OOS log-loss/Brier specifically on the Average (T3) tier — the report's blind spot (Brier 0.2619)* — rather than the tree overfitting historical variance.

**Pass/fail bar (locked, all required):**
1. Overall OOS log-loss Δ ≥ +0.002 **and** bootstrap 95% CI excludes 0.
2. **T3-tier subset** Brier improves by ≥ 0.005 with CI excluding 0 (the targeted hypothesis).
3. Feature importances sign-correct (higher own-ecosystem → higher win prob; negative Interval_Delta → lower) and no dominant sign flip.
4. No tail-variance inflation vs the frozen baseline.
- **PASS** → swap Model B in for H2-season (with calibration re-check). **NULL** → keep the frozen baseline; harvest the edge via the **post-processing decompression wrapper** instead (the shadow), itself gated by its own OOS calibration test. **KILL** → drop. *Prior NULL on the SP-micro-feature class means the default expectation is skeptical; the ecosystem feature must clear the bar on its own merits.*

**Why this ordering:** the prior micro-feature backtest returned NULL, so unproven rolling/correlated features into a live tree right before three unattended weeks = overfit + calibration-break risk for zero proven gain. Track 1 captures the edge immediately at zero risk; Track 2 decides the retrain on evidence.

---

## Track 2b — Shadow decompression γ-tuning protocol (PRE-REGISTERED 2026-06-17, before any July fitting)

**Purpose:** decide the shadow's decompression strength γ (live v0 = 0.35, untuned) **and** whether it must scale with run environment — without falling into the regression trap of reading a global miscalibration as a localized `pred_total` interaction. Strict ordering: establish the main effect, *then* test for an interaction. Data = the accumulating `rating_shadow_<date>.json` logs (now carrying `raw_home_prob`, `shadow_decompressed_prob`, `pred_total`, `total_line`, `realized_total`/outcome).

**Skeptical prior:** the SP-micro-feature backtest returned NULL. The overlay must *earn* a win before any second-order knob is tuned. If Phase 1 fails, the answer is "harvest via a properly-calibrated wrapper, or drop" — **not** "tune the interaction."

### Phase 1 — Main-effect optimization (global γ)
- Grid γ ∈ {0.0, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60} over the **walk-forward** folds (train-fold picks γ that minimizes train Brier; score the held-out fold only). **Never** minimize in-sample Brier on the full set (that overfits the single parameter — the same trap as the prior backtest).
- γ=0 is the frozen baseline by construction, so it's the control arm.
- **Gate (locked):** the OOS-selected γ* PASSES only if `Brier(frozen + Shadow(γ*))` strictly beats `Brier(frozen)` with a 1000-sample game-level bootstrap 95% CI on ΔBrier that **excludes 0**; report log-loss + a reliability/calibration curve alongside.
- **If FAIL/NULL → STOP.** Do not proceed to Phase 2. Keep frozen prod; revisit the wrapper idea only under a fresh pre-reg.

### Phase 2 — Residual diagnosis (only if Phase 1 PASSES)
- Compute the **post-γ\* per-game calibration residual** (e.g., `outcome − shadow_decompressed_prob(γ*)`), NOT |Δ|. Regressing |Δ| on `pred_total` just re-derives the known logit shape and proves nothing.
- Regress those residuals on `pred_total` (and check `total_line` as a robustness alt). 
- **Add a dynamic γ(total) damping term ONLY IF** the residual-vs-total slope is (a) significant (95% CI excludes 0), (b) sign-stable across a fold split, and (c) practically meaningful. Otherwise keep global γ* — the data did not demand the interaction.
- Any γ(total) form adopted is itself re-validated OOS before it could ever leave shadow status.

**Promotion:** a tuned shadow only graduates from display-to-applied via a separate calibration-wrapper pre-reg; it never silently starts adjusting picks. Production XGBoost remains the Track-2 (Model-B) question, independent of this overlay.

---

*Read-only. Frozen model/weights/stake untouched. Thresholds frozen; changes require a dated amendment here.*
