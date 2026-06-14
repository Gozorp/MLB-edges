# SKIP Audit — Pre-Registered Shadow/Ablation Protocol (2026-06-14)

**Status:** dated, pre-registered audit plan. **No July validation data has been used to design it.**
Freeze discipline: **nothing in the live staking path changes before July**; everything here runs in
**shadow / counterfactual** mode first. Thresholds, ablations, and decision rules are fixed now.

## 0. The principle (what this audit is and is not)

SKIP is probably doing useful bankroll defense, especially against upper-band overconfidence. The
goal is **not** to find a looser gate because it blocked the most winners during the 6/5–6/14 cold
stretch — that's how you overfit to recent pain. The only clean question is:

> **Which single gate, relaxed marginally while every other gate stays frozen, would have improved
> risk-adjusted net return *and* calibration on out-of-sample data — without worsening blowout-tail
> risk?**

Measured by **net units / CLV / calibration / tail**, never by raw skipped-winner count. Every
conservative filter eats some winners; that alone proves nothing.

## 0.5 Baseline finding (verified 2026-06-14) — the book is dormant, not exposed

Before designing relaxations, the live state was measured directly over the frozen era (6/4–6/14):

- **Tier counts:** SKIP 72 · GOLD 48 (stake_mult 0) · PLATINUM 10 · DIAMOND 3.
- **Moneyline bets actually placed: 0.** Every one of the 13 DIAMOND/PLATINUM picks was killed
  upstream by the Layer-2 **edge gate** (e.g. 6/14 COL@OAK PLATINUM at −17.48pp; 6/12 MIA@PIT
  DIAMOND at +2.23pp), not by the HARD CAPs.

This **refutes** the "unprotected max-unit bets got crushed" hypothesis: no bets fired. The cold
stretch is 100% paper (directional picks). The HARD CAPs being display-only was operationally moot —
the edge gate had already zeroed every stake. So the audit's real target is **gate conservatism**
(is the engine never firing, leaving a clean modest-edge band on the table?), not cap exposure. The
gate is also doing genuine work — it correctly rejected the −17pp OAK model-vs-market disagreement —
so the fix is not "drop the +4 floor," it's "find whether a narrow, market-supported GOLD/edge-near-
miss band (§6) deserves a tiny shadow stake." This is the corrected framing for everything below.

## 1. Verified architecture — there are TWO surfaces, audit them separately

Confirmed in source (not assumed):

- **Surface A — moneyline stake / true SKIP.** Driven by `score_conviction(...) → tier →
  TIER_SIZES[tier]` (`edge_calculator.py` / `backtesting.py`; GOLD=0.00, PLATINUM=0.30, DIAMOND=1.00)
  **plus** the Layer-2 eligibility gates in `main_predict.py` (`MIN/MAX_MODEL_PROB` [0.48,0.72],
  `MIN_FAIR_PROB` 0.42, `MIN/MAX_EDGE_PCT` [4,15]pp). A pick is a true no-bet when its tier multiplier
  is 0 **or** any gate fails. (Optional: `USE_LEARNED_CONVICTION` swaps TIER_SIZES for a logistic
  stake from `models/conviction.json`.)
- **Surface B — parlay-leg / Top-Probable-Outcomes grade.** `parlay_builder.grade_picks` →
  `grade_score` → letter, with the HARD CAPs (1–10) demoting it.

**Correction to the design doc (verified):** the HARD CAPs that "force score→0" (caps 3, 6, 9, 10)
map to grade **"C"** (`_score_to_grade: ≥0 → C`), not a true moneyline SKIP. They gate **parlay legs
and the TPO card** — they do **not** override the moneyline `stake_mult`, which was already set
upstream by the conviction tier + Layer-2 gates. So:

- To audit **what actually got/​didn't get bet** → ablate **Surface A** (tier + Layer-2 gates).
- To audit the **parlay/TPO** surface → ablate **Surface B** (the caps).

Conflating them is the first mistake to avoid.

## 2. Data tiers — triage vs. evidence vs. decision

| Window | Role | Allowed use |
|--------|------|-------------|
| Last ~10 days | **triage only** | spot candidates to investigate; never select a threshold |
| Full frozen ledger (6/4→) | **preliminary evidence** | rank gates by *repeated* negative marginal value |
| Future July / live-shadow ledger | **decision evidence** | the only data that may justify a live change |

The 6/5–6/14 stretch (52–71, 42%, Brier 0.219) is emotionally salient and is **triage, full stop.**

## 3. Per-skipped-pick data schema (log this for every blocked pick)

```
game_id · date · pick_side · pick_prob · fair_prob · market_prob_open · market_prob_close ·
edge · tier · stake_mult · why_skipped · pre_cap_score · pre_cap_grade · final_grade ·
cap_hit · closing_odds · result · unit_return · run_margin
```

**Attribute by skip reason, not by pre-cap quality.** A `pre_cap_grade=A` blocked by `edge=+28pp`
(calibrator hallucination) is a totally different animal from a `pre_cap_grade=A` blocked by
`fair_prob=0.41` (borderline market-sanity). Same grade, opposite failure modes.

**Cleanest attribution = single-binding-reason picks**: rows where *exactly one* rule blocked the
bet. Those isolate a gate's marginal effect; multi-reason rows are noise for attribution.

## 4. Marginal ablation — the real test (Surface A)

For each gate, relax it **slightly, alone, everything else frozen**, and measure only the
*incremental* picks that become stakeable.

```
MAX_MODEL_PROB:  0.72 → 0.74 ; 0.72 → 0.76        [SHADOW-ONLY — upper band]
MAX_EDGE_PCT:    +15 → +17.5 ; +15 → +20          [SHADOW-ONLY — upper band]
MIN_EDGE_PCT:    +4  → +3
MIN_FAIR_PROB:   0.42 → 0.40
GOLD tier:       stake_mult 0.00 → 0.10 shadow, only if all Layer-2 gates pass
Cap 10 (B):      score→0 becomes score→1 / →2 in shadow only
Caps 3 / 6 / 9:  DO NOT loosen live — shadow audit only
```

Per ablation, track: `N_added · win_rate · avg_odds · unit_ROI · CLV · Brier · ECE · avg_model_prob
· avg_market_prob · avg_edge · max_drawdown · P(loss 3+/5+/7+)`.

**Winner = the relaxation that adds positive net units AND CLV without worsening calibration or
tail loss.** Not the gate that "blocked the most wins."

## 5. Do-not-touch-live list (upper-band anti-calibration gates)

Until the July calibration re-test resolves, these stay frozen live and are **shadow-only**:

```
MAX_MODEL_PROB = 0.72   ·   MAX_EDGE_PCT = +15pp   ·   Cap 3   ·   Cap 6   ·   Cap 9
```

These are directly tied to the known suspected defect (upper-band overconfidence). Letting more
75–85% favorites through *before* calibration is the exact wrong "sweet spot."

## 6. Where the sweet spot probably is — the GOLD-clean hypothesis

The promising bucket is boring, market-supported, modest-edge picks the system refuses for having
only one signal or barely missing a floor — **not** extreme-chalk overrides. Priority order:
(1) GOLD-but-clean picks, (2) fair-prob floor near-misses, (3) edge lower-bound near-misses,
(4) non-zero cap demotions that may be too punitive, (5) bottom-bucket rules where the **market
agrees**.

**Pre-registered shadow candidate (Tier-1 runnable):**

```
GOLD+ shadow stake (0.10x, shadow only):
  tier == GOLD
  pick_prob ∈ [0.54, 0.68]
  fair_prob ≥ 0.45
  edge ∈ [+5, +12] pp
  no HARD CAP 3 / 6 / 9
  no pick-side bullpen-disadvantage cap (Cap 7)
  closing market does not move materially against the pick   [Tier-2 — needs closing data]
```

This tests whether GOLD=0 leaves value on the table **without** opening the upper-band door.

## 7. Decision rule (not binary — sample is thin)

- **GREEN:** the relaxation adds positive net units + non-negative CLV, with ECE/Brier non-worse
  and no tail-risk increase, on **decision-tier** (July/shadow) data.
- **YELLOW:** positive point estimate but wide CIs / thin n → keep in shadow, re-probe.
- **RED / no-go:** negative net units, CLV erosion, calibration worse, or tail-loss up → keep the
  gate as-is.

Only GREEN on decision-tier data justifies a live change — and even then, ship the *smallest* gate
move that earned it, one gate at a time.

## 8. Data-availability gate (honest constraint)

The audit's CLV / closing-line metrics need data we **don't capture today**: Kalshi is the only
moneyline source and it's logged at **slate-build time, not close**; the multi-book odds API is
dead; no CLV pipeline exists.

- **Tier-1 (runnable now):** model probs, Kalshi build-time `fair_prob`, edge, tier, grade, result,
  run-margin, and the single-binding-reason attribution + the GOLD+ shadow candidate (sans the
  closing-move filter). Report ROI/calibration/tail on this; **omit CLV** and state so.
- **Tier-2 (needs infra):** `market_prob_close`, `closing_odds`, CLV, "market moved against" —
  gated on building closing-odds capture (blocked by the odds-API outage). Do not claim CLV results
  from Tier-1 data.

## 9. Freeze discipline

No live gate, tier, cap, or `stake_mult` changes before July. The shadow ledger logs counterfactual
stakes alongside live; the audit reads it; live behavior only changes after a GREEN on decision-tier
evidence, smallest-move-first. Pre-registered here, before the data.
