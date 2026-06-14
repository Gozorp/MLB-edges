# SKIP — Executive Staking Layer (design)

**Status:** descriptive design doc assembled from the live code (read-only). There is **no single
"SKIP module"** — SKIP is the executive risk layer that sits *on top of* the frozen model and
decides **"don't stake this pick."** It is implemented across three files:

- `mlb_edge/config.py` — the thresholds and the tier→stake multiplier table.
- `mlb_edge/main_predict.py` (~L385–520) — the bet-eligibility **gates** that write `why_skipped`.
- `mlb_edge/parlay_builder.py` — the **grade + HARD CAP** engine (`grade_picks`, `_score_pick`).

Important framing: the **frozen model always produces a directional pick** (the favored side) for
every gradeable game. SKIP never changes *which side* — it only decides **whether to stake it and
how big**. A game showing `tier SKIP -> stake_mult=0` is a deliberate "no bet," not a missing
prediction. Under the freeze, all of this logic is frozen (`--skip-weights`); the caps and gates
are the executive layer, unchanged.

---

## Layer 1 — Conviction tier → stake multiplier (`config.py`)

A pick's conviction **tier** comes from how many independent signals fire. The tier maps to a
multiplier on the Kelly stake (`config.py` L94–104):

| Tier | Stake mult | Meaning |
|------|:----------:|---------|
| DIAMOND | **1.00** | 3+ signals fire — the elite tier |
| PLATINUM | **0.30** | 2-signal tier (re-enabled after the v9-vs-v12 diagnostic) |
| GOLD | **0.00** | single-signal — *still dropped*, too noisy to stake |
| (lower / SKIP) | **0.00** | no stake |

So in practice **only DIAMOND and PLATINUM are bet**; GOLD and everything below resolve to
`stake_mult = 0` — i.e., SKIP. That's why the diags routinely read `tier GOLD -> stake_mult=0`:
the pick is real, the tier just isn't stake-worthy.

Slate-level cap: **`MAX_DAILY_RISK_UNITS = 15.0`** (per-slate structural ceiling on total staked
units; was tightened to 10 for travel and restored to 15 on 6/13).

---

## Layer 2 — Bet-eligibility gates (`main_predict.py` ~L385–520)

Independently of tier, a pick is forced to SKIP (`stake_mult=0`) and a human-readable reason is
appended to `why_skipped` whenever it fails any gate. Thresholds from `config.py`:

| Gate | Rule | Constant | Value |
|------|------|----------|:-----:|
| Model-prob band | `pick_prob` must be inside the band | `MIN_MODEL_PROB / MAX_MODEL_PROB` | **[0.48, 0.72]** |
| Fair-prob floor | market (Kalshi) fair prob must clear floor | `MIN_FAIR_PROB` | **≥ 0.42** |
| Edge band | model−market edge must be inside the band | `MIN_EDGE_PCT / MAX_EDGE_PCT` | **[+4, +15] pp** |
| Tier | non-stakeable tier | (Layer 1) | GOLD/below → 0 |

The matching `why_skipped` strings (verbatim from the code) are:

- `model_prob {p:.3f} outside [0.48,0.72]` — extreme longshot or extreme chalk (value compression).
- `fair_prob {f:.3f} < 0.42` — market doesn't rate the side highly enough.
- `edge {e:+.2f}pp outside [4,15]pp` — too small to be real, or too big to be trustworthy.
- `tier {tier} -> stake_mult=0` — conviction tier isn't stake-worthy.

The **rationale for the band edges**: a sub-4pp edge is indistinguishable from noise; a >15pp edge
against a tight MLB closing line is almost always the model (or its calibrator) hallucinating, not
genuine value. The model-prob ceiling at 0.72 deliberately avoids the overconfident upper bands
that the calibration work flagged.

---

## Layer 3 — Grade + HARD CAP engine (`parlay_builder.py`)

Separately from staking, every pick gets a **letter grade** (display + parlay-eligibility). This
is where most "looks like a good pick but SKIP" outcomes are decided.

### Base score (`_score_pick`)
Starts at 0 and adds:

- **+1** an F-signal fires (F1 SP xERA gap / F2 lineup xwOBA gap / F3 swing-take gap),
- **+2** the SP edge (xERA) agrees with the pick,
- **+1** PQI (pitching-quality index) confirms the side (`|pqi_diff| ≥ 3.0`),
- **+1** Stage 1 (first-5) and Stage 2 (full-game) agree,
- **±1** team-quality modifier *(currently DISABLED → +0)*.

Score → grade (`_score_to_grade`): `≥5 A · ≥4 A- · ≥3 B+ · ≥2 B · ≥1 B- · ≥0 C · else D`.

### Odds-API guard
If `fair_prob` is missing (no market context), the grade is **capped at C** — without an external
market check the model has no sanity rail on its own conviction.

### HARD CAPs (the demotion stack)
A set of backtested rules that **demote or zero** the score after the base scoring — each was added
in response to a documented loss pattern, and each carries its validation note in the code:

| Cap | Trigger | Effect |
|-----|---------|--------|
| 1 | Negative-edge on a GOLD-or-better pick (`edge<0`, score≥3) | → 1 |
| 2 | `F3>1000` + `p_model>0.65` without an elite opposing SP (xERA<4.0) | → 3 |
| 3 | PLATINUM calibration artifact: `p_model>0.85` + Stage 1/2 delta>0.20 | → 0 (SKIP) |
| 4 | Stage 1/2 delta≥0.12 **and** `confidence_downgrade=True` | → 1 |
| 5 | F1* (thin-Statcast SP) as the *only* signal | → 2 |
| 6 | Extreme edge `>+25pp` = calibrator hallucination | → 0 (SKIP) |
| 7 | Pick-side bullpen disadvantage (`hl_bullpen_xwoba_gap≥0`, score≥3) | → 1 |
| 8 | Bottom-bucket marginal favorite (`pick_prob∈(0.50,0.52]`, `edge≤+12pp`) | → 1 |
| 9 | Top-bucket `pick_prob>0.80` = calibrator hallucination | → 0 (SKIP) |
| 10 | Bottom-bucket `pick_prob∈(0.50,0.55]` without a Claude CONFIRM | → 0 (SKIP) |

(Plus earlier guards: no-signal-but-high-score, edge<−8 on score≥3, large Stage gap on score≥5.)
A `pre_cap_score` / `pre_cap_grade` is recorded alongside the final grade so the weekly backtest
can monitor whether the caps are over-restricting (e.g. lots of would-be-A picks capped to D that
went on to win).

**Important correction (verified 2026-06-14):** the HARD CAPs operate on **Surface B (the grade)**,
not the moneyline stake. Caps 3/6/9/10 setting `score→0` map to grade **"C"** (`_score_to_grade`:
`≥0 → C`), which gates **parlay legs + the Top-Probable-Outcomes card** — they do **not** override
the moneyline `stake_mult`. The actual moneyline SKIP is decided upstream by the conviction tier
(`TIER_SIZES[conv.tier]`) + the Layer-2 gates. So: the caps are a parlay/display demotion; the
moneyline no-bet is Layer 1 + Layer 2. Several caps (3, 6, 9) still matter as the executive layer's
stand-in for the upper-band overconfidence that the July calibration re-test targets — but on the
grade/parlay surface, not the single-game stake. See `SKIP_AUDIT_SPEC_2026-06-14.md` for what this
means when auditing each surface.

---

## How the layers combine (decision flow)

```
frozen model → directional pick + pick_prob (raw)
      │
      ├─ Layer 2 gates: model_prob∈[0.48,0.72]? fair_prob≥0.42? edge∈[4,15]pp?
      │        any fail → why_skipped += reason, stake_mult = 0  (SKIP)
      │
      ├─ Layer 3 grade: base score → letter; HARD CAPs demote/zero
      │        caps 3/6/9/10 → score 0 → SKIP-grade
      │
      └─ Layer 1 tier: signals → tier → stake mult (only DIAMOND 1.0 / PLATINUM 0.3 bet)
                 + slate cap MAX_DAILY_RISK_UNITS = 15
```

A pick is **staked** only if it clears every Layer-2 gate **and** lands in a stake-worthy tier
**and** survives the Layer-3 caps. Anything else is SKIP. This is intentionally conservative — the
whole design assumes a thin directional edge, so it sits out far more than it bets.

## Related: display-side consumers
- The dashboard **Top Probable Outcomes** card is grade-first and **drops SKIP / D–F** game picks.
- The **Edge Calculator** panel mirrors the Layer-2 edge band ([4,15]pp, with the 4–8pp
  "Goldilocks" display tier) so you can hand-check a market line against the gate.

---

## Source map (the actual "files")

| Piece | File | Where |
|-------|------|-------|
| Tier → stake mult, all thresholds | `mlb_edge/config.py` | L94–168 |
| Bet-eligibility gates + `why_skipped` | `mlb_edge/main_predict.py` | ~L385–520 |
| Grade scale + base score + HARD CAPs | `mlb_edge/parlay_builder.py` | `_score_pick`, `_score_to_grade`, `grade_picks` |

*Want the raw source instead of this synthesis? Say the word and I'll send `parlay_builder.py`,
`config.py`, and the `main_predict.py` gate block directly.*
