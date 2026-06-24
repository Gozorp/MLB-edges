# Why the model "went south" the past 7 days — a diagnosis (2026-06-17 → 06-24)

**Author:** analysis run 2026-06-24. **Scope:** the perceived good→bad swing over the last week.
**Bottom line up front:** the prediction model itself **did not change and cannot have "gone south"** — it has been frozen since June 3rd. What changed in this window was the **infrastructure around the model** (a dead started-game lock, failing pipeline runs, an odds-feed outage, two input-feature refreshes) plus an ordinary **variance cluster** of lost one-run games. The losses are real; a model regression is not the cause.

---

## 1. The record didn't fall off a cliff — it bounced

Per-day pick record (from the OOS ledger; 6/22 corrected to finals because the ledger's auto-scoring lags a day):

| Date  | Record | Win% | Note |
|-------|--------|------|------|
| 06-16 | 8–5    | 62%  | good |
| 06-17 | 6–5    | 55%  | fine |
| 06-18 | 2–5    | 29%  | **bad** |
| 06-19 | 7–6    | 54%  | fine |
| 06-20 | 3–9    | 25%  | **worst** |
| 06-21 | 6–4    | 60%  | **good** |
| 06-22 | 4–6    | ~40% | below |
| 06-23 | 4–7    | ~36% | below (still finalizing) |

**Aggregate over all logged days: 106–102 = 51.0% raw**, with the **GOLD tier still ~57%**.

This is the single most important shape in the data: it **oscillates**. A model that had genuinely degraded would trend *down* and stay down. Instead, the worst day (6/20, 25%) is immediately followed by one of the best (6/21, 60%). Good and bad days are interleaved around a stable ~51% mean. That is the fingerprint of **variance**, not decay.

---

## 2. The model itself was frozen — proof, not assertion

A regression in the model is ruled out by direct inspection of state:

- `data/state/weights_freeze.json` → `{"frozen": true, "since": "2026-06-03", "until": "2026-07-20"}` (the SFO→Japan travel freeze).
- `data/state/weights_state.json` → **last modified June 13**, byte-identical since. The production weights that made every pick from 6/17–6/24 are the same weights.
- The nightly chain runs `predict.py --skip-weights`, so weights are never recomputed at predict time.
- Daily self-learn still *fires* but is muted: every entry in the recent `recalibration_log_*` files is a **no-op** (`"diff": {"error": "no new picks file"}`). It is bumping harmlessly into the freeze guardrail.
- Production serves **raw** probabilities — the isotonic calibrator is a July-only pre-registered test, not live.

**A static model cannot drift on its own.** So "what changed to make it worse" has to be answered from *around* the model, not inside it.

---

## 3. What actually changed/was enabled in the window (the real answer)

From the deployment history (`git log`, 6/17→6/24), here is everything that touched the system, sorted by whether it could plausibly affect *results* vs. just the display.

### 3a. Things that COULD have affected picks or the record

These are the legitimate "what changed" culprits.

- **Dead started-game lock — fixed 6/20 (`8f20349` "Fix dead started-game lock + trim hourly cron (stop post-game pick corruption)").**
  Before this fix, the started-game lock wasn't actually firing, so the **hourly cron would re-run and overwrite the picks of games that had already started** with fresh post-start data. You witnessed this directly ("the model 30 mins prior to the game refreshed the previous refreshed slate"); the anchor case was a pick whose edge swung from +8 to −9.39 *after* first pitch. **Effect:** for the early part of this window (~6/17–6/20), some "picks" that later got graded were *mutated mid-game* — so part of that record is grading corrupted entries, not the model's real pre-game calls. This makes the model **look** worse than it was. Fixed and verified 6/20.

- **Pipeline runs were failing — fixed 6/19 (`49241f3`).**
  The `daily-slate.yml` push-retry block had been truncated by a filesystem write artifact, so **scheduled and manual runs were erroring out** with "unexpected end of file." **Effect:** around 6/18–6/19 the slate could publish **stale** (older lineups/probables), meaning picks were made on outdated inputs. Resolved 6/19.

- **6 bugs fixed from a code audit — 6/18 (`6cc27e1`).**
  Unspecified correctness fixes landed mid-window. Anything they fixed was, by definition, behaving wrong *before* 6/18.

- **Input-feature refreshes (frozen weights, but the inputs moved):**
  - **Umpire effects DB rebuilt — 6/22 (`71c00bd`).** The model consumes umpire features (`ump_k_pct_delta`, `ump_bb_pct_delta`). Rebuilding that DB shifts those inputs, so predictions can move even with frozen weights.
  - **Weekly calibrator refit — 6/21 (`0dda96e`).** A scheduled refit ran; under the freeze its output is gated/no-op, but it is worth noting as a thing that *fired* in the window.

- **Odds-feed outage — 6/21.** On 6/21, **13 of 14 games had no market odds** (see table below). With no market line, the Odds-API guard caps grades at C and edges can't be computed properly. **Effect:** the *tiering/grading* on 6/21 was distorted (not the win/loss of the pick, but how it was classified and staked).

Input-quality by day (note the 6/21 odds collapse and the 6/20–6/21 stress spikes):

| Date  | games | pending_SP | odds_fetched | odds_missing | stress_flagged |
|-------|-------|-----------|--------------|--------------|----------------|
| 06-17 | 14    | 2         | 8            | 6            | 3 |
| 06-18 | 8     | 1         | 6            | 2            | 1 |
| 06-19 | 14    | 0         | 12           | 2            | 2 |
| 06-20 | 14    | 0         | 10           | 4            | **8** |
| 06-21 | 14    | 1         | **1**        | **13**       | **13** |
| 06-22 | 12    | 0         | 12           | 0            | 6 |
| 06-23 | 15    | 1         | 14           | 1            | 3 |
| 06-24 | 16    | 2         | 13           | 3            | 4 |

### 3b. Things that changed but CANNOT have affected results (display-only)

The large majority of this week's commits are **frontend, freeze-safe, zero model effect** — they change what you *see*, not what the model *picks*:

- Slate overlay/drawer rebuild and widening (6/21), mobile fit fixes (6/22), preview full-width (6/22), bullpen-fatigue ERA column + headshots + full-pen display (6/20–6/22), lineup-edge `__ppProj` fallback (6/22), Conditional Risk Factor narrative (6/21), MC incoherence chip (6/22), HR-props lineup fallback (6/19).
- Docs/prereg: July park-incoherence pre-registration (6/22), edge-tightening prep (6/20) — offline only.

None of these touch `predict.py`, the weights, the calibrator, or the tiering math. They are explicitly labeled "display-only, freeze-safe" and were verified as such.

---

## 4. The dominant driver of the actual losses: variance

Stripping away the measurement noise from §3a, the *real* losses on the bad days were lost **close games**, which is the textbook signature of variance rather than a broken model:

- **6/20 (the 3–9 day) went 1–3 in one-run games.** One-run games are decided by sequence luck (a bloop with runners on vs. bases empty), not by a misjudgment of true team talent. A fundamentally broken model loses by *wider* margins as it systematically misranks teams — that is not what happened.
- **Small-sample math.** On a ~12-game slate, a true-talent 51% book going 3–9 has a binomial probability of about **2.4%** — roughly a 1-in-40 day. Over a 162-game season you are essentially *guaranteed* several of these, and they cluster by chance.
- **The edge is intact where it should be.** GOLD held ~57% while raw sat at 51%. The model still knows when it has an edge; the confidence tiers are still separating signal from coin-flips. A genuine degradation would compress that gap.

---

## 5. Putting it together — why it *felt* like good→bad

Two unrelated things overlapped and created the impression of a decline:

1. **A genuine variance cluster** — 6/18, 6/20, and 6/23 lost the close ones — landing in the same week and feeling like a trend (it isn't; 6/21 was a 60% day right in the middle).
2. **Transient infrastructure bugs early in the window** — a dead started-game lock corrupting in-progress picks and a failing slate pipeline publishing stale data — which *mis-recorded* part of that early-week performance and were both fixed by ~6/19–6/20.

Neither is a model regression. There is **nothing in the model to roll back or re-tune**, and doing so off this week would be the classic over-fitting-to-noise trap.

---

## 6. The real forward risk (and the one gap to close)

The genuine danger of a frozen model is **not** overfitting — it's **staleness**: weights pinned to a June-13 snapshot slowly diverge from reality as the season moves. Watch this approaching the freeze end (July 20):

- **Bullpen churn** — fatigue and minor-league shuttles mean July bullpens differ from early-June ones.
- **Roster/role shifts near the trade deadline** — call-ups, shutdowns, repositioned assets.
- **Injuries** — a frozen model only adjusts if the manual override / data feed catches the roster change.

Over 7 days staleness is negligible; by mid-July it is the thing to monitor.

**The monitoring gap:** the health system alerts on *infrastructure* (did it publish? did it deploy? is data complete?) but has **no automated alert on realized performance** — no GOLD-tier win-rate tripwire. Today that's a manual ledger spot-check, and the only automated performance number (the chain's Brier) is *in-sample*. For an unattended travel window, the recommended (read-only, freeze-safe) addition is a **trailing-30 GOLD-tier tripwire** that pings the existing Discord webhook only if GOLD breaches a variance-aware floor (~44%, ~2σ below the 57% base) — sample-gated so it can't fire on a single 0–4 day.

---

## Recommendations

1. **Change nothing in the model.** It is frozen by design and the week's results do not justify intervention.
2. **Treat the early-week record (≈6/17–6/20) as partly corrupted** by the now-fixed started-game-lock and pipeline bugs — don't read it as model signal.
3. **Build the GOLD-tier tripwire** before traveling (pre-register the threshold + sample gate up front).
4. **Re-evaluate at July 20** for staleness, not noise — that's when a frozen model legitimately needs a refresh.

*Evidence base: `git log` 2026-06-17→06-24; `data/state/weights_freeze.json` + `weights_state.json`; `recalibration_log_2026-06-*`; OOS ledger (`docs/data/oos_ledger.jsonl`); per-day `picks_*_diag.csv`. Read-only analysis; frozen model untouched.*
