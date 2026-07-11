# INPUT_INTEGRITY_PREREG.md — coverage soft-cap + spread-aware edge
**Status: PRE-REGISTERED 2026-07-10 (Architecture Pre-Flight Rule 2). Thresholds
locked below BEFORE any evaluation data is examined. No live behavior changes
until the freeze lifts AND the gates below pass. Instrumentation (the
`feature_coverage_<date>.json` sidecar + Kalshi spread capture) starts
accumulating now; the decisions run on that accumulated OOS data.**

Companion flaw-remediation shipped same day (no prereg needed — pure data
plumbing): standings snapshot feed un-staled (78 days → same-day via
`tools/refresh_standings_snapshot.py`, statsapi-sourced in bref format,
chained pre-predict).

---

## A. Input-coverage grade soft-cap

**Flaw:** the model emits identically-confident probabilities whether its
inputs were rich or degraded (observed: bakes running on 78-day-old standings,
FanGraphs 0/4, Savant 35/42, with no confidence consequence).

**Candidate rule (display/grade layer ONLY — never the booster):** a bake is
LOW-COVERAGE iff any of: `savant_ok < 30/42`, `bref_age_days > 7`,
`with_market < 50% of games`. On a LOW-COVERAGE bake, the executive grade is
ceilinged at **B** and stake_mult × **0.5** for that slate.

**Evaluation (July, OOS, on the sidecar history + graded picks):**
- Partition graded picks by their bake's coverage status (needs **n ≥ 25**
  low-coverage picks; else extend the window, not the claim).
- SHIP iff low-coverage picks are measurably worse: pick-level Brier gap
  ≥ **+0.03** vs full-coverage AND win-rate gap ≤ **−5pp**, both directions
  consistent. Else **NULL** → keep the sidecar as telemetry only.

## B. Spread-aware edge qualification

**Flaw:** `fair_prob` comes from the Kalshi bid/ask midpoint, but a 5¢-wide
market and a 1¢-wide market are treated as equally trustworthy anchors; edge
measured against a wide spread is partly noise.

**Candidate rule (edge-qualification layer ONLY):** a pick's edge only
qualifies for the [4,15]pp betting band if `edge_pp ≥ 4 + spread_pp/2`
(i.e., the edge must clear half the bid-ask spread on top of the floor).

**Evaluation (July, OOS):**
- Bucket graded picks by anchor spread (tight ≤ 2pp / mid 2–4pp / wide > 4pp;
  needs **n ≥ 30** in the wide bucket).
- SHIP iff wide-bucket staked picks underperform tight-bucket by ROI gap
  ≥ **10pp** OR wide-bucket edge shows no positive calibration
  (realized win rate ≤ fair + 1pp). Else **NULL**.

## Discipline
Same contract as every prereg in this repo: thresholds above may not move
without re-signing; no partial ship; NULL results are recorded and respected;
both rules live OUTSIDE the frozen booster (grade/stake/qualification layers);
harnesses must be read-only until the decision.
