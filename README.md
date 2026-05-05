# mlb_edge

A two-stage XGBoost MLB betting prediction system with a multi-layer
parlay-grading architecture. Pulls Statcast and FanGraphs data, joins live
odds from the-odds-api.com (with an ESPN fallback), applies eight independent
safety checks to each pick, and emits a daily slate of game predictions and
parlay recommendations.

> **Disclaimer:** This is a research / educational project documenting model
> architecture and rule design. It is not financial or betting advice. Sports
> wagering is governed by local laws — consult those before acting on any output.

---

## What it does

For every MLB game on a given slate, the model:

1. **Scrapes** Baseball Savant Statcast leaderboards (40+ endpoints) and
   FanGraphs SP dashboards.
2. **Pulls** the day's MLB schedule, probable pitchers, and live odds.
3. **Scores** each game through a two-stage XGBoost pipeline:
   - **Stage 1** (F5 / first-five-innings): SP-driven probability
   - **Stage 2** (Full game): adds bullpen and lineup leverage
4. **Grades** each pick through eight safety rules (see below).
5. **Recommends** parlays subject to a profile-diversity constraint.
6. **Writes** a `picks_<date>_diag.csv` with full reasoning + a
   `parlay_<date>.txt` human-readable report.

---

## Design principles

1. **SP first, everything else second.** Stage 1 predicts first-five-innings
   (F5) run expectancy from pitcher xERA, xwOBA-allowed, K-BB%, and SIERA.
   Stage 2 takes Stage 1 output as a feature and adds bullpen, offense, and
   context.
2. **Convergence-based conviction.** Picks require multiple independent
   signals (F1 xERA gap, F2 xwOBA gap, F3 swing-take gap, F4 pitcher luck)
   to agree. Single-signal edges get skipped.
3. **Market edge is a gate.** Model probability > Vegas implied probability
   is necessary but not sufficient — the edge must survive the conviction
   filter and pass all eight safety checks.
4. **No leakage, ever.** Time-series CV with a hard date cutoff. Rolling
   features use `.shift(1)` before aggregation.

---

## The grading rubric

Each pick gets a base score, then modifiers:

| Signal | Range | What it catches |
|---|---|---|
| Clears all bet-eligibility gates | +3 | Model has confidence + edge in trade band |
| PLATINUM tier (gated by SP-edge agreement) | +2 / +1 / 0 (withheld) | Lineup-driven conviction, *only* if SP edge confirms |
| F-signal fires (F2 xwOBA gap, F3 swing-take gap) | +1 | Lineup conviction layer fires |
| SP edge agrees with pick (small-sample halved) | +2 / +1 / -2 / -1 | Pitcher quality + sample reliability |
| **PQI** (Pitching Quality Index, bullpen-aware) | +1 / 0 / -1 | Late-game pitching degradation |
| **Team-quality modifier** | +1 / 0 / -1 | Win record, last-10 form, offensive RPG gap |
| Stage 1 / Stage 2 agreement | +1 / -1 / -2 | F5-vs-Full coherence |

### Hard caps

| Cap | Effect |
|---|---|
| **Odds-API guard** | If `fair_prob` is missing (Odds API didn't fire), score is capped at 0 (C grade). Without market validation we have no external check on overconfidence. |
| **Compound-small-sample cap** | If both SPs have < 60 BF, the SP-edge layer is unreliable enough that score is capped at 3 (B+ max). |
| **F-signal-required A cap** | If no F-signal fires, score is capped at 4 (A- max). Pure-pitching wishful thinking can't reach top tier without lineup confirmation. |

### Score → Grade

| Score | Grade | Recommendation |
|---|---|---|
| ≥ 5 | A | Parlay anchor |
| ≥ 4 | A- | Parlay-worthy |
| ≥ 3 | B+ | Stretch leg, max 1 per ticket |
| ≥ 2 | B | DO NOT PARLAY |
| ≥ 1 | B- | DO NOT PARLAY |
| ≥ 0 | C | DO NOT PARLAY |
| < 0 | D | DO NOT PARLAY |

### Parlay diversity reserve

Anchors are capped at **2 chalk + 2 contrarian** per ticket, where:
- **chalk** = picked side is the market favorite (`fair_prob ≥ 0.50`)
- **contrarian** = picked side is the market underdog (`fair_prob < 0.50`)

This prevents 3+ chalk or 3+ contrarian tickets from sharing the same
correlated failure mode.

---

## Module layout

```
mlb_edge/
├── savant_scraper.py      # Baseball Savant CSV scraper (40+ endpoints)
├── fangraphs_scraper.py   # FanGraphs SP leaderboard scraper
├── odds_fallback.py       # ESPN public odds page parser (fallback)
├── data_ingestion.py      # the-odds-api.com client + flatten helpers
├── live_news.py           # SP scratches, ump assignments, bullpen-short
├── injury_news.py         # IL placements + lineup-scratch detection
├── feature_engineering.py # Per-game feature computation
├── calibration.py         # BinnedIsotonicCalibrator
├── learned_conviction.py  # Stake-multiplier learner
├── pitching_quality.py    # PQI: composite SP+BP quality index
├── team_quality.py        # Team record / last-10 / RPG modifier
├── parlay_builder.py      # Grader + parlay assembly with diversity rule
├── model_registry.py      # Versioned model snapshots
├── main_predict.py        # Slate scoring entry point
└── ...

predict.py                  # CLI: `python predict.py 2026-05-06`
docs/                       # Static dashboard (GitHub Pages)
```

---

## How to run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your Odds API key (free tier from the-odds-api.com)
echo "ODDS_API_KEY=YOUR_KEY_HERE" > .env

# 3. Run today's slate
python predict.py

# Or a specific date
python predict.py 2026-05-06

# Skip scraping (use cached data)
python predict.py --skip-all-prep

# Bets-only output (skip the diagnostic table)
python predict.py --bets-only
```

Output:

- `picks_YYYY-MM-DD_diag.csv` — full diagnostic with grades, scores, reasoning
- `picks_YYYY-MM-DD_news_overrides.csv` — news-layer audit log
- `parlay_YYYY-MM-DD.txt` — human-readable parlay report

---

## Architecture notes

**Two-stage XGBoost.** Stage 1 (F5) and Stage 2 (Full game) train on different
feature sets. F5 is dominated by SP performance + early-inning lineup quality;
Full incorporates bullpen state, fatigue, and late-game leverage.

**Calibration.** `BinnedIsotonicCalibrator` applies Bayesian shrinkage and PAV
monotonic isotonic regression over score bins. This corrects for the model's
tendency to be overconfident at the extremes.

**Conviction signals.** F2 (xwOBA gap), F3 (swing-take gap), F5 (bullpen state)
are derived from Savant data and used as discrete tier indicators. The
learned conviction module replaces hand-tuned tier multipliers with a
fitted stake-sizing function.

**News layer.** Late-breaking news (SP scratches, lineup changes, bullpen
fatigue, sharp line movements) is applied as a per-game probability override
between scoring and grading. The audit trail is written separately so every
modifier is inspectable.

---

## Status

Active research project. The architecture has been stress-tested on slates
from late April through early May 2026. Recent additions (May 2026):

- **Odds-API guard** — caps grade at C when market data is missing
- **PQI module** — bullpen-aware pitching quality with leverage weighting
- **Team-quality modifier** — record / form / RPG gap as new signal
- **Compound-small-sample cap** — both SPs < 60 BF → grade B+ max
- **F-signal-required A cap** — no lineup signal → A- max
- **Diversity reserve** — max 2 chalk + 2 contrarian anchors per parlay
- **ESPN odds fallback** — when the-odds-api.com fails
- **FanGraphs scraper** — daily SP xERA / Stuff+ / Pitching+ pull

A 5/1 backtest (the model's worst day, 4-9 record) shows the new architecture
would have either prevented all losses (the Odds-API guard fires when
fair_prob is missing) or capped the contrarian wipeout via the compound-SS
and F-signal caps.

---

## Live dashboard

A static dashboard is published via GitHub Pages from the `docs/` folder.
It loads slate CSVs and the parlay report client-side and renders the
graded slate plus an interactive query box (e.g., type *"ATL vs SEA"*,
*"best pick"*, *"contrarian"*, *"A grade"*, *"NYY"*).

### Architecture (private-repo friendly)

The dashboard fetches data from `docs/data/` (relative path), not from
`raw.githubusercontent.com`. A GitHub Action (`.github/workflows/bake-data.yml`)
copies `picks_*_diag.csv` and `parlay_*.txt` from the repo root into
`docs/data/` on every push, so the deployed Pages site is self-contained.

This means:
- Repo can be **public or private** (Pages from private repos requires GitHub Pro)
- The dashboard works without exposing your repo's raw URL
- No browser-side cross-origin tricks

### Auto-update workflow

`.github/workflows/daily-slate.yml` runs `predict.py` daily at 07:00 UTC
(via cron) and commits the new slate CSV. The bake-data workflow then
fires on the new commit, the dashboard auto-refreshes.

To enable: add an `ODDS_API_KEY` secret in **Settings → Secrets and
variables → Actions**. Without it, the workflow runs but produces a slate
with no `fair_prob` column (Odds-API guard caps everything at C grade).

## License

MIT — see [LICENSE](LICENSE) · Repo: <https://github.com/Gozorp/MLB-edges>.
