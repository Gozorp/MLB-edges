# "O/U Totals" (Over/Under) feature — design files & how it works

> **READ THIS FIRST — the totals model is PAUSED (2026-06-03).** Its run
> predictor (`pred_runs`) carries **essentially no signal: r = 0.05 (R² 0.3%)**
> against actual total runs on 118 graded games, vs the market line's r = 0.33.
> The pre-registered recalibration backtest returned **NULL** (you can't calibrate
> a zero-signal predictor), and the model is badly under-dispersed (it regresses
> every game toward ~9 runs). The O/U section still *renders* and *ranks*, but its
> edges are not trustworthy. **Do not bet totals** until the rebuild
> (`TOTALS_REBUILD_PLAN.md`) ships and beats the closing line OOS. Moneyline is
> unaffected (separate model, Kalshi-anchored).

Like the moneyline feature, O/U is two layers: a **backend model** that decides the
side and writes a CSV, and a **frontend** that ranks and displays it. The display
layer doesn't pick a side — it reads what the model wrote.

| Layer | File | Role |
|-------|------|------|
| **Side / line / edge** | `mlb_edge/main_totals.py` (+ `models/totals_latest.pkl`, `choose_side`/`kelly_stake`) | projects runs, picks OVER/UNDER vs the market total, writes `docs/data/picks_totals_<date>.csv` |
| **Ranking + display** | `docs/index.html` (`_topTotals`, `_narrateTotal`, `_totalStatus`, the per-game O/U pill) | fetches the CSV → `window.__totalsByMatchup`, ranks, renders |
| **Status / roadmap** | `TOTALS_REBUILD_PLAN.md` | why it's paused + the rebuild |

The hand-off file is `docs/data/picks_totals_<date>.csv`.

---

## 1. Backend — how the side is decided (`mlb_edge/main_totals.py`, 693 lines)

A separate XGBoost model from the win model. CLI mirrors `predict.py`:
```
python -m mlb_edge.main_totals --mode train  --seasons 2023,2024,2025 --save models/totals_latest.pkl
python -m mlb_edge.main_totals --mode predict --date <D> --out picks_totals_<D>.csv
python -m mlb_edge.main_totals --mode backtest --season 2025 --out bt_totals_2025.csv
```

**Predict path (`run_predict`):**
1. Project `pred_runs` for every slate game from the totals model.
2. **If a market total + odds are available:**
   `chosen = choose_side(pred, line, over_dec, under_dec)` → `side` (over/under) and
   `edge_runs` (how far `pred_runs` diverges from the market `total_line`). Then derive
   probabilities from the market:
   ```
   edge_bump = min(0.02 * edge_runs, 0.10)            # small confidence nudge, capped
   book_fair = p_over_fair  (or p_under_fair)          # devigged market prob for the side
   our_prob  = clamp(book_fair + edge_bump, 0.01, 0.99)
   stake     = kelly_stake(our_prob, decimal, TOTALS_KELLY_FRACTION)
   ```
   So `our_prob` is the **market's fair prob nudged by the model's run edge** — not a
   from-scratch probability. `edge_pp` downstream = `(our_prob − book_fair) * 100`.
3. **If no market odds** (the common case now — see §4): **pred_runs-only mode** —
   the row is still written with `pred_runs`, but `total_line / side / edge_runs /
   our_prob / book_fair` are left empty, and the dashboard renders "Model: X.X runs
   (no market)".

**Overlays (observability, not the production number):** `pred_runs_bvp_adjusted`
(batter-vs-pitcher delta), and `pred_winp_mc` / `pred_runs_mc` (Monte-Carlo shadow).
`pred_runs` stays the production projection.

**CSV schema (`picks_totals_<date>.csv`):**
`game_date, home_team, away_team, total_line, pred_runs, edge_runs, side, decimal,
our_prob, book_fair, stake_units, book, pred_runs_bvp_adjusted, total_runs_delta,
home/away_runs_delta, home/away_bvp_n_pa, home/away_bvp_ops_shrunk,
bvp_signal_strength, player_aware_signal, pred_winp_mc, pred_runs_mc`

---

## 2. Frontend — ranking & display (`docs/index.html`)

**Load:** `fetch('./data/picks_totals_<date>.csv')` → parse → `window.__totalsByMatchup`
keyed by `"away @ home"` (null on failure; the O/U section just doesn't render).

**Rank — `_topTotals(totalsByMatchup, slateRows)`** (the O/U section of Top Probable
Outcomes):
```javascript
.map(t => ({ matchup, side, line: total_line, prob: our_prob,
             edge_pp: (our_prob - book_fair) * 100, pred_runs }))
.filter(rows with a finite our_prob and a line)
.sort((a, b) => Math.abs(b.edge_pp) - Math.abs(a.edge_pp));   // |edge| DESC
```
Placement is by **absolute edge vs book-fair** — biggest disagreement with the market in
*either* direction tops the section (unlike Game Picks, which is grade-first since
`29d5302`). `.slice(0, 5)`.

**Narrate — `_narrateTotal(item)`:** "OVER 8.5 on A @ B at N% model probability (+Xpp edge
vs book-fair). Model projects P total runs vs line of L."

**Grade — `_totalStatus(item, results)`:** once final, `total = away + home`;
`OVER → HIT if total > line`, `UNDER → HIT if total < line`, equal → `PUSH`; plus
`LIVE / PREGAME / POSTPONED / TBD` states.

**Per-game pill** (in the slate row): tooltip `pred Xr · Vegas Y · stake Zu`.

---

## 3. The exact answer to "how the side is chosen"

`pred_runs` (model) vs `total_line` (market): if the model projects **more** runs than
the line → **OVER**; **fewer** → **UNDER**; the gap is `edge_runs`. The displayed
probability is the market's devigged fair prob for that side, nudged up slightly by the
edge. The O/U card ranks by how far the model's probability sits from the market's.

## 4. Why you can't trust it right now (the part that matters)

- **No signal:** `pred_runs` r = 0.05 vs actual (market 0.33). Recal backtest = NULL.
  Under-dispersed (pred SD ~0.5–1.1 vs actual 4.18) — it predicts ~9 runs for everything.
- **No reliable market line:** every free/keyless odds source is dead (the-odds-api
  lapsed 5/21; ESPN 202; DK 403). So totals frequently run in **pred_runs-only mode**
  (no side, no edge) — and when a line *is* present, `our_prob` is mostly the market's
  own number echoed back.
- **Roadmap:** `TOTALS_REBUILD_PLAN.md` — Step 0 restore a market feed (free-tier
  the-odds-api Starter key), Step 1 a bottom-up `E[home runs] + E[away runs]` model
  (SP/bullpen run-prevention, Log5 lineup vs the arm, park/weather/umpire), fix the
  under-dispersion, then pre-registered OOS validation vs the closing line. **Do not bet
  totals until it beats the line OOS.**

> Net: the O/U feature is fully wired and renders, but it's the system's weakest leg by
> design admission. Treat the moneyline (Game Picks) as the trustworthy product; treat
> O/U as paused pending the rebuild.
