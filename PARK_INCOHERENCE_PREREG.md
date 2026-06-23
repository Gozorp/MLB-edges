# Park-Specific Incoherence Test — Pre-Registration (LOCKED 2026-06-22)

**Status:** Pre-registered under Rule 2 (all gates, definitions, and park list locked BEFORE
any test code runs). Freeze-safe: this document changes nothing. **Execution: July 2026,
post-return.** No model code, no pipeline change, no dashboard change is authorized by this doc.

**Origin:** The 2026-06-22 post-loss backtest (`MLB_Edge_Backtest_Memo_2026-06-22.docx`)
rejected a *general* incoherence/confidence cap — the headline-vs-MC disagreement pattern
(headline >= .65 while Monte-Carlo <= .55) historically WINS 58.8% (n=17), so capping it loses
EV. The single surviving hypothesis is that the pattern may be uniquely dangerous in
**extreme-variance parks** (Coors and similar high-run environments), which the 18-slate window
was too small to isolate. The 6/22 anchor failure (BOS @ COL, 91% headline / 51% MC, lost by 1)
occurred in exactly such a park. This test asks whether that is a real park-specific effect or
just that one game.

---

## 1. Hypothesis (one, directional, locked)

> Among **incoherent confident picks**, the picked side underperforms **specifically in
> extreme-run parks** relative to all other parks — i.e., the headline model's confidence is
> least trustworthy when the Monte-Carlo engine disagrees AND the game is in a high-variance
> run environment.

Null: incoherent confident picks perform no worse in extreme parks than elsewhere.

## 2. Locked definitions (cannot change after this date)

- **Incoherent confident pick (the subject set):** a graded game where
  `pick_prob >= 0.65` AND `pred_winp_mc <= 0.55` (pred_winp_mc is the pick-side prob —
  convention verified 2026-06-22 against the OOS ledger; Brier 0.247). This is the same
  pattern the display badge flags and the backtest measured.
- **Extreme-run park set (FROZEN candidate list — may NOT grow after today):**
  Coors Field (COL) [PRIMARY], Great American Ball Park (CIN), Globe Life Field (TEX),
  Fenway Park (BOS), Chase Field (AZ), Kauffman Stadium (KC), Camden Yards (BAL).
  - **Qualification rule (locked):** a candidate park is "extreme" iff its 3-year run park
    factor on the single pre-named source — **Baseball Savant park factors
    (baseballsavant.mlb.com/leaderboard/statcast-park-factors), runs, most recent 3-yr** —
    is **>= 105**. The candidate LIST is frozen now; the >=105 cut is applied once at unblinding.
    Coors qualifies unambiguously and is the **primary** analysis regardless of the others.
- **Outcome:** `pick_correct` from the OOS ledger result rows (non-void, scored).
- **Park label:** derived from each game's `venue` via statsapi `gamePk` — recoverable
  retroactively for every historical date, so **NO live pipeline change is needed** (this is
  why the test is freeze-safe; park is reconstructed in July from data we already key on).

## 3. Data

- Source: `docs/data/oos_ledger.jsonl` (scored result rows) joined to `docs/data/picks_*_diag.csv`
  (pick_prob, pred_winp_mc) joined to statsapi venue by (slate_date, matchup).
- Window: 2026-06-04 (ledger start) through the July execution date — accrues ~5-6 more weeks
  of games than the 6/22 read.
- **Minimum-sample gate (locked):** require `n_extreme >= 25` incoherent-confident picks in the
  extreme set (Coors-primary may be reported at lower n but is descriptive-only below 25).
  If `n_extreme < 25` at execution time, the result is **INCONCLUSIVE — re-defer**, not a pass.

## 4. The test (single pre-specified comparison)

Primary metric: win% of the picked side among incoherent-confident picks, **extreme-park subset
vs. all non-extreme parks**.

**CONFIRMED** iff ALL of:
1. `win%(extreme) <= 0.45`, AND
2. `win%(non-extreme) - win%(extreme) >= 0.10` (>= 10pp gap, in the hypothesized direction), AND
3. bootstrap 95% CI of that gap **excludes 0** (10,000 resamples, seed locked = 7), AND
4. `n_extreme >= 25`.

Otherwise **NULL** (no effect) or **INCONCLUSIVE** (gate #4 fails). A NULL or INCONCLUSIVE result
means the incoherence indicator stays **display-only forever** and this hypothesis is closed.

Secondary (descriptive only, never decision-bearing): same comparison for Coors-only;
calibration gap (mean pick_prob − realized win%) in each subset.

## 5. Remedy IF (and only if) CONFIRMED

- Allowed: a **park-gated confidence shrink** — for incoherent confident picks in the frozen
  extreme set only, blend the displayed/staked confidence toward `pred_winp_mc` by a bounded,
  walk-forward-fit factor. Must itself pass a separate walk-forward OOS check (non-inferior
  log-loss, sign-correct) BEFORE shipping.
- Forbidden (regardless of result): any global confidence cap, any per-team penalty weight,
  any change that touches non-extreme parks, any retro-tuning of the gates above.
  (See `[[project_stake_gates_rejected]]` — executive-layer gates already failed OOS.)

## 6. Anti-overfit guardrails

- Park candidate list, subject definition, PF source/cutoff, all four gates, bootstrap seed:
  **locked by this document.** No peeking, no adding parks, no moving thresholds.
- Exactly one primary test. Report NULL/INCONCLUSIVE honestly; do not fish secondaries for a story.
- The 6/22 BOS @ COL game is the motivating anchor but is INSIDE the data; the verdict rests on
  the full accrued sample, not that game.

---

## Related monitoring (manual note — NOT automation)

**PLATINUM tier watch.** As of the 6/22 read PLATINUM sat at 42.1% win (n=19) while GOLD was
56.6% (n=76). Sample is far too small to act on. **Action: eyeball PLATINUM's rolling tier win%
periodically as the ledger grows — no scheduled task, no code.** If it is still materially below
50% at meaningful n (>= ~50) after the trip, open a separate pre-registered calibration probe
then. Until then this is "keep collecting," not a reaction.
