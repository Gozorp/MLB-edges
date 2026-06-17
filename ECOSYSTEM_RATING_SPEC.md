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

*Read-only. Frozen model/weights/stake untouched. Thresholds frozen; changes require a dated amendment here.*
