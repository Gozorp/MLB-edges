# "Top Probable Outcomes" — what determines it & how the team is chosen

**Short answer:** *Top Probable Outcomes does **not** choose a team.* The team is
decided upstream by the frozen win model (`pick = the side the model gives ≥50%
win probability`). Top Probable Outcomes is a **display layer** that takes the
picks the model already made, **ranks** them by edge-vs-market, and shows the top 5.
So there are two layers, in two files:

| Layer | File | Role |
|-------|------|------|
| **Team choice** (which team) | `mlb_edge/model.py` + `mlb_edge/main_predict.py` | the frozen model picks the side; writes `pick`, `pick_prob`, `edge_pp` into `docs/data/picks_<date>_diag.csv` |
| **Ranking + display** | `docs/index.html` (`renderTopProbableOutcomes`, `_topGameMLPicks`, `_topTotals`, `_topPitcherKs`) | reads the diag, ranks, renders the card |

`docs/data/picks_<date>_diag.csv` is the hand-off between them.

---

## 1. How the team is chosen (backend — frozen model)

The model (`mlb_edge/model.py`) is a calibrated XGBoost classifier. Its
`predict_proba(x)[:, 1]` outputs **`full_prob` = the HOME team's win probability**.

In `mlb_edge/main_predict.py`, the pick side is locked purely on that probability —
**the team the model gives ≥ 50% is the pick** (i.e. the model's favorite):

```python
# main_predict.py  — pick-side selection
if pd.notna(full_p) and full_p >= 0.5:
    side, p_model, fair = "home", full_p, fair_h
    picked = home_abbr
else:
    side, p_model, fair = "away", 1 - full_p if pd.notna(full_p) else float("nan"), fair_a
    picked = away_abbr
```

Column semantics written into the diag (from the same file's docstring — *do not
reinvert*):

```
full_prob : home-perspective probability from the model
p_model   : PICK-perspective probability (= full_prob if pick==home, else 1 - full_prob)
pick_prob : explicit alias of p_model (always pick-perspective)
fair_prob : Shin-devigged market probability for the PICK side (NaN if no odds)
edge_pp   : (p_model - fair_prob) * 100   — model edge over the market, in points
```

So per game the diag carries: **`pick`** (the team), **`pick_prob`** (model's win
prob for that team), **`fair_prob`** (market's devigged prob), and **`edge_pp`**
(how much the model disagrees with the market, in its favor). The executive grader
then assigns `grade`/`tier` and the F5/full split, but the *team* is already fixed
by the ≥0.5 rule above.

---

## 2. How "Top Probable Outcomes" ranks & places them (frontend — display only)

`docs/index.html`. The card has three sections; each is an independent ranking of
the diag rows. **None of them re-decide the team** — they read `r.pick` and sort.

### Game Picks — grade-gated, ranked grade-first then edge  (UPDATED 2026-06-13, commit 29d5302)
```javascript
function _topGameMLPicks(rows) {
  const GW = { "A": 6, "A-": 5, "B+": 4, "B": 3, "B-": 2, "C": 1 };
  return rows
    .map(r => {
      const edge = parseFloat(r.edge_pp);
      const prob = parseFloat(r.pick_prob);
      const grade = (r.grade || "").toString().trim();
      const tier = (r.tier || "").toString();
      if (!isFinite(edge) || !isFinite(prob)) return null;        // need model + market
      if (edge <= 0) return null;                                 // model must beat the market
      if (tier.includes("PENDING_SP_DATA")) return null;          // SP not announced
      if (tier.toUpperCase().includes("SKIP")) return null;       // model said don't bet
      if (!(grade in GW)) return null;                            // drops D / F / ungraded
      return { pick: r.pick, matchup: r.matchup, prob, edge_pp: edge,
               tier: r.tier, grade, _gw: GW[grade], ... };
    })
    .filter(Boolean)
    .sort((a, b) => (b._gw - a._gw) || (b.edge_pp - a.edge_pp));  // grade first, edge tiebreaker
}
```
**Why this changed (data 6/2–6/13):** the original pure `edge_pp DESC` sort surfaced
the grader's *own rejects* — 27 of 36 top-3-by-edge slots were C/D, and most per-slate
#1s were D/SKIP (incl. the SEA@BAL D/SKIP). Empirically high edge does **not** mean low
confidence (corr(edge, prob)=+0.38; the highest-edge quartile averaged 63.8% win prob),
so a confidence floor was the wrong lever. The fix respects the executive grade: exclude
SKIP tiers + D/F, rank grade-first. The team shown (`item.pick`) is still the diag `pick`;
placement is now **grade, then edge**, then `.slice(0, 5)`. A non-SKIP C backfills thin
slates so the card is never empty.

### O/U Totals — ranked by |edge| vs book-fair
```javascript
function _topTotals(totalsByMatchup, slateRows) {
  // ... per matchup: edgePp = (our_prob - book_fair) * 100 ...
  .sort((a, b) => Math.abs(b.edge_pp || 0) - Math.abs(a.edge_pp || 0));
}
```

### Pitcher Strikeouts — ranked by expected Ks (no market line)
```javascript
function _topPitcherKs(rows) {
  // for each announced SP:  expected_K = (sp_k_pct / 100) * 26   (≈ batters faced)
  // pushes {name, team, matchup, expected_K, p_over_5/6/7, ...}
  return out.sort((a, b) => b.expected_K - a.expected_K);
}
```

### Assembly
```javascript
function renderTopProbableOutcomes(rows, totalsByMatchup, results) {
  const gameMLs = _topGameMLPicks(rows).slice(0, 5);   // top 5 by edge
  const totals  = _topTotals(totalsByMatchup, rows).slice(0, 5);
  const ks      = _topPitcherKs(rows).slice(0, 5);
  // ... renders three sections; each card title uses item.pick / item.side / item.name
}
```

---

## The exact answer to "how the model determines the team for that place"

1. **Team** = the side the frozen XGBoost win model rates ≥ 50% (`full_prob ≥ 0.5`
   → home, else away). That decision lives in `mlb_edge/main_predict.py`; the
   probability comes from `mlb_edge/model.py`'s `predict_proba`.
2. **Whether it appears in Top Probable Outcomes, and where** (as of 29d5302), is
   decided in `docs/index.html` → `_topGameMLPicks`: keep only rows with positive
   model **edge** (`edge_pp > 0`), a real probability, an announced SP, a non-SKIP
   tier, and an **A/B/C grade** (D/F dropped); then **rank by grade first, edge as
   tiebreaker** and take the top 5. The #1 slot is the model's highest-conviction
   pick that also beats the market — not merely the largest market disagreement
   (which, pre-fix, surfaced the grader's own C/D-SKIP rejects).

> Note: the *Game Picks* placement is edge-driven (model vs market), while the
> O/U section is |edge|-driven and the Pitcher-K section is pure expected-Ks
> (no market). All three are read-only views of the frozen model's output —
> changing them is a display change, never a model change.
