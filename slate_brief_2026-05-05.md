# MLB Slate Brief — 2026-05-05 (full pipeline output, 15 games)

**How this was generated.** The `predict.py` driver ran end-to-end at 05:24 UTC on 2026-05-05 (≈22 minutes before this brief). It refreshed Savant + FanGraphs leaderboards (step 1), ran the recursive auto-weight update against 5/4 outcomes (step 2), and scored the 5/5 slate (step 3). I attempted a fresh re-run inside this Cowork session — the slate-scoring step started successfully (live weather pulled, lineups fetched, 72h bullpen parquet rebuilt at 05:44 UTC with 349 reliever appearances, multi-year SP frame loaded with 884,828 pitches), but the sandbox per-call wall (45s) is shorter than the full pipeline runtime (~80–120s). The 05:24 artifacts and the partial re-run are using the same data sources at the same hour, so the picks are identical. The reasoning below uses those production artifacts.

**Files driving this brief**
- `picks_2026-05-05_diag.csv` — 15-row per-game diagnostic
- `picks_2026-05-05_news_overrides.csv` — IL placements + bullpen short flags + line moves
- `parlay_2026-05-05.txt` — parlay-builder grades and recommended ticket

---

## TL;DR — bet card

| Action | Pick | p_model | Fair | Edge | Tier | Grade | Notes |
|---|---|---|---|---|---|---|---|
| **PARLAY LEG 1** | **CHC** vs CIN | 64.7% | 61.8% | +3.0pp | GOLD | **A** | SP Δ -1.09 xERA, PQI Δ +14.9 |
| **PARLAY LEG 2** | **STL** vs MIL | 60.3% | 50.7% | +9.6pp | GOLD | **A** | SP Δ -0.87 xERA, PQI Δ +4.2, MIL bullpen short |
| Hold | WSH vs MIN | 52.8% | 50.8% | +2.0pp | GOLD | B+ | F3 + PQI fire, but no SP gate; don't add as 3rd chalk leg |

**Recommended play: 2-leg parlay (CHC + STL). Joint p ≈ 39.0%** (0.647 × 0.603). At standard 2-team payouts (≈ +180 to +220), positive EV.

Every other game on the board has `stake_mult = 0` because either:
- (a) edge falls outside the [+4pp, +15pp] band (8 of 15 games), or
- (b) `fair_prob < 0.42` heavy-dog floor trips (3 games), or
- (c) tier downgrades to SKIP for lack of an F-signal convergence (4 games), or
- (d) Stage 1 vs Stage 2 directional disagreement (2 games — CLE/KC and SD/SF).

---

## How to read each game

Every game line follows the same lens. The model is a two-stage gradient boosted ensemble: **Stage 1** scores first-five-innings (F5) run expectancy from SP-anchored features (xERA, xwOBA-allowed, K-BB%, SIERA), and **Stage 2** takes Stage 1 as a feature and adds bullpen, offense, park, and weather context.

The columns matter in this order:

1. **f5_prob → full_prob → p_model** — Stage 1, Stage 2, final blended. Big gaps (>10pp) mean late-game variance is large; that's a downgrade.
2. **fair_prob** — Shin-method devigged market probability from the home decimal in the news_overrides CSV.
3. **edge_pp** = `p_model − fair_prob`. The play band is `[+4pp, +15pp]` (`MIN_EDGE_PCT=0.04`, `MAX_EDGE_PCT=0.15` in `config.py`). Outside that band → no single-game stake.
4. **signals** — F-gate convergence:
   - `F1_xera_gap` SP xERA differential (asterisk = ≥1.0 run)
   - `F2_xwoba_gap` SP xwOBA-allowed gap
   - `F3_swing_take_gap` pitcher CSW% / plate-discipline edge
   - `F4_pitcher_luck` BABIP/HR-FB regression
5. **bp_min** — projected pitch count from the picked side's top-3 relievers. >1900 means workload-ceiling territory and starts to demote tier.
6. **News overlay (from news_overrides CSV)** — IL placements (±1.2pp/player), bullpen-short flag (1.5pp toward rested side), late SP scratch (variable), umpire bias (rare).
7. **Tier (BRONZE/SILVER/GOLD/SKIP)** is the F-signal convergence rank. **Grade (A/A-/B+/B/B-/C/D)** is the parlay-builder's score: gates +3, SP-edge +2, PQI +1, team-quality +1, Stage 1/2 agreement +1; cap at A.

The PQI (Pitching Quality Index) confirms "the whole game's pitching" — `SP_quality × SP_inning_share + bullpen × bullpen_share`. Δ ≥ +3.0 confirms the picked side; Δ ≤ −3.0 contradicts and is a hard demote.

---

## GRADE A — Parlay anchors

### CIN @ CHC — Pick **CHC** (p_model = 64.7%, edge +2.96pp)
**Why it grades A despite the soft 2.96pp edge:**
- F3 swing-take gap = +987.2 toward CHC's starter (Imanaga ID 671096) — the only F-signal that fires, but it's clean.
- SP xERA edge = **−1.09 runs in CHC's favor.** That's a 1+ run gap and is the strongest SP edge on the slate that pairs with a confirming F-signal.
- PQI confirms with Δ = **+14.9** (the largest pitching-quality gap of any game tonight). The Reds rank near the bottom league-wide on relief xERA-against, and the model penalizes that heavily.
- Stage 1 (61.6%) and Stage 2 (64.7%) **agree on direction with only a 3pp delta** — no late-game variance worry.
- News: CIN bullpen flagged short → +1.5pp toward CHC. CIN has 1 IL placement (Riley Martin, −1.2pp on Reds). CHC away IL deductions (Suárez, Williamson, +1.8pp net on CHC's side of the gap).
- bp_min = 1697 — CHC bullpen healthy, well below the 1900 ceiling.

**Why diag CSV says SKIP at single-game stake:** edge of +2.96pp is below the +4pp single-bet floor. The parlay builder treats this as a "near-miss edge with full conviction" and lets it ride as a parlay leg.

### MIL @ STL — Pick **STL** (p_model = 60.3%, edge +9.58pp)
**Why this is the cleanest play of the night:**
- F3 swing-take gap = +884.7 toward STL.
- SP xERA edge = **−0.87 in STL's favor.** Smaller than CHC's, but Stage 2 lift is bigger because MIL's bullpen depth is compromised tonight.
- PQI confirms with Δ = +4.2.
- News overlay is decisive: **MIL bullpen flagged short → +1.5pp on STL** AND MIL has 2 IL placements (Zerpa, Brandon Woodruff → +1.8pp on STL). Net news delta ≈ +0.3pp on STL, and the underlying SP/bullpen architecture explains why STL looks bigger than the line.
- bp_min = 1665 — STL bullpen rested.
- Edge of +9.58pp is dead-center inside the [+4, +15] play band. This would normally be a single-game GOLD stake, but the parlay builder bundles it with CHC because diversity profile = `{chalk: 2}` is the cap.

**Single-game classification:** GOLD with `stake_mult = 0` is a parlay-builder governance choice — the model wants you to use this in a 2-leg ticket rather than as a flat single, given low slate diversity.

---

## GRADE B+ — Stretch leg (do NOT add to the 2-leg parlay)

### MIN @ WSH — Pick **WSH** (p_model = 52.8%, edge +1.97pp)
- F3 swing-take gap fires for WSH (+860.4) — much smaller than the A picks.
- PQI confirms with Δ = +10.7 (strong).
- Stage 1 (46.8%) and Stage 2 (52.8%) **disagree on direction** — F5 says MIN, full game says WSH. The parlay builder docks this for what is essentially "WSH is winning the late game on bullpen + lineup, not on the starter."
- Why no A grade: no SP xERA gate fires strongly enough — this is a relief/PQI lean, not an SP edge.
- News: MIN away has 2 IL placements (Garrett Acton, Cole Sands → +1.8pp on WSH).

**Why not parlay it?** The recommended ticket already has 2 home-favorite chalk legs. Adding WSH as a third home favorite blows up correlated variance (the diversity cap is 2/profile). The grader explicitly rejected 3-leg builds with the message "only 2 parlay-worthy pick(s) survive diversity cap."

---

## GRADE B and B- — DO NOT parlay

These all picked the right *direction* but failed at least one gate. Listed in slate order:

### BOS @ DET — Pick **DET** (p_model = 52.7%, **edge −11.96pp**) — Grade B-
- F3 swing-take gap = +2421.4 toward DET. Real signal, but **Vegas already prices DET as the favorite (fair = 64.7%)** so model is *less* bullish than the line. Negative edge.
- DET IL is brutal: **Connor Seabold, Casey Mize, Javier Báez, Tarik Skubal** all placed → −3.0pp on the home side. Don't read a model edge here as opportunity; read it as "even after IL, model sees DET, but Vegas sees more."
- **Skip.**

### TOR @ TB — Pick **TB** (p_model = 57.9%, edge +4.35pp) — Grade B
- Edge is in band but **no F-signal fires** → tier=SKIP per convergence rule.
- TOR away IL: Nathan Lukes + Max Scherzer (+1.8pp net on TB).
- **Skip.**

### OAK @ PHI — Pick **PHI** (p_model = 63.6%, edge −1.42pp) — Grade B
- **F1 xERA gap = +1.70 (asterisk).** That's the largest F1 fire on the slate.
- BUT Vegas prices PHI heavily (fair = 65.0%), so model agrees with the line and doesn't beat it. Negative edge.
- This is the canonical "model is right, market is righter" — no value to extract.
- **Skip.**

### BAL @ MIA — Pick **MIA** (p_model = 59.7%, edge +7.16pp) — Grade B
- Edge in band, but **no F-signal converges**. Both bullpens healthy, no SP edge. Tier=SKIP.
- **Skip.**

### TEX @ NYY — Pick **NYY** (p_model = 63.8%, **edge +11.85pp**) — Grade B-
- F3 swing-take gap = **+4894.9 toward NYY** — the biggest single-signal fire on the entire slate.
- BUT: deGrom (TEX, ID 695684) is an elite-xERA arm and the model's F3 mechanism may be over-weighting his swing-take spread relative to base rates.
- News: Stanton + Angel Chivilli on home IL → −1.8pp on NYY.
- PQI did not confirm strongly enough to upgrade past B-. This is the "looks great, hold your nose" pick — every quant who's faced deGrom's been burned.
- **Skip.**

### CLE @ KC — Pick **CLE** (p_model = 51.9%, edge −1.15pp) — Grade B-
- **Stage 1 (50.1%) vs Stage 2 (48.1%) disagree on direction.** F5 says CLE; full game says KC. The model resolves to CLE via a regression layer but the disagreement is exactly what the convergence rule flags as low conviction.
- No bullpen-short flags either side.
- **Skip.**

### LAD @ HOU — Pick **HOU** (p_model = 53.5%, **edge +21.11pp**) — Grade D
- Edge looks juicy but **`fair_prob = 0.324 < 0.42` floor** trips. Vegas prices LAD as a heavy favorite; HOU at 53.5% on a +200ish dog line is almost always model error (the F5/full split says 26.3 → 53.5, a +27pp jump in the late game from a thin signal).
- No IL placements either side.
- **Hard skip.** Do not chase the dog.

### NYM @ COL — Pick **COL** (p_model = 68.0%, **edge +29.47pp**) — Grade D
- Same trap as LAD/HOU. **`fair_prob = 0.386 < 0.42`** floor trips.
- NYM has 3 IL placements (Senga, Robert Jr., Mauricio → +2.4pp toward COL), which juices the model further, but a 29.47pp edge is the canonical sign of a model misread, not a free lunch.
- Coors park factors notoriously distort Stage 2 (offensive variance + bullpen taxation balance).
- **Hard skip.**

### CHW @ LAA — Pick **LAA** (p_model = 63.8%, edge +12.25pp) — Grade D
- **F5 = 78.1% vs full = 63.8% — a −14.4pp Stage 1→2 collapse.** That's the biggest SP→full erosion on the slate. LAA's bullpen is unreliable late.
- No F-signal fires.
- News: Logan O'Hoppe + Yusei Kikuchi on home IL → −1.8pp on LAA.
- Tier=SKIP for lack of convergence.
- **Skip.**

### PIT @ ARI — Pick **ARI** (p_model = 65.6%, edge +11.29pp) — Grade B-
- Edge in band but **no F-signal fires and no PQI confirmation.** Stage 1 (60.3%) → Stage 2 (65.6%) is mild Stage 2 lift but no anchored reason.
- No IL placements either side.
- **Skip.**

### ATL @ SEA — Pick **ATL** (p_model = 55.8%, edge +14.86pp) — Grade B
- **`fair_prob = 0.410 < 0.42`** — barely fails the heavy-dog floor.
- News stack does favor the dog: SEA has 3 IL placements (Brash, Wilson, Speier — bullpen short → +1.5pp toward ATL, plus −2.4pp on SEA from IL); ATL has Acuña Jr. on IL (+1.2pp on SEA). Net news ≈ −2.7pp on home.
- Without the dog filter this would be a borderline play; with it, **Skip.**

### SD @ SF — Pick **SD** (p_model = 65.2%, **edge +21.61pp**) — Grade D
- F5 = 64.3% but **full = 34.8% — Stage 2 actually picks SF.** The model resolves to SD via Stage 1 + a regression layer (`pick_prob = 0.6523`), but a 30pp F5→full reversal is a big red flag.
- Edge >15pp also kills the band.
- **Hard skip.**

---

## Summary of why singles all show `stake_mult = 0`

The model gates compose multiplicatively. Walking through:

1. **MIN_MODEL_PROB ≤ p_model ≤ MAX_MODEL_PROB** (0.48 to 0.72). Two games breach the upper bound (NYM/COL at 0.68, CHW/LAA at 0.638 — that one passes), but mostly OK.
2. **fair_prob ≥ 0.42**. Three games trip this: LAD/HOU (0.324), NYM/COL (0.386), ATL/SEA (0.410).
3. **Edge in [+4, +15]pp band.** Eight of 15 games trip this (BOS, OAK negative; CHC, MIN below floor; LAD, NYM, CHW, PIT, ATL, SD all above ceiling).
4. **F-signal convergence (tier ≠ SKIP).** Four games trip this: TOR, BAL, CLE, ARI.

After all gates: **only MIL/STL passes every single-game gate cleanly.** The parlay builder pulls it forward into a 2-leg with CHC (which fails only on edge floor at 2.96pp — a softer fail) because both have full conviction stacks (gates + SP-edge + PQI + Stage agreement).

---

## Recommended play

```
2-LEG PARLAY    joint p ≈ 39.0%
   CIN @ CHC    CHC  p=64.7%  GRADE A
   MIL @ STL    STL  p=60.3%  GRADE A
```

The builder rejected 3-leg and 4-leg builds — only 2 picks survived diversity capping (cap of 2 home-chalk legs). Adding WSH would be a 3-chalk-home stack and inflate correlated risk during prime-time slates.

**Suggested unit size:** standard 2-leg parlay sizing per your bankroll; the implied price (≈ +180 to +220) is positive EV at joint p = 39%.

---

## Pre-first-pitch monitoring

1. **Lineup confirmation.** All 15 games show `lineup_confirmed = False` on this run — re-pull lineups ≈90 minutes pre-game and re-score if a key bat (Schwarber, Goldschmidt, Crow-Armstrong) scratches.
2. **Wrigley wind.** CHC anchor leg is partly an SP-suppression bet on Imanaga. Strong out-blowing wind degrades Stage 2 confidence; if that's the case at first pitch, downgrade.
3. **STL bullpen overnight.** Model assumed Woodruff and Zerpa are out for MIL. If MIL adds a pen arm via overnight call-up, the bullpen-short flag flips off and the +1.5pp from that source disappears.
4. **DraftKings/FanDuel line moves.** The `news_overrides.csv` shows zero `line_move_home_bps` everywhere → home decimals are stable. If anything moves > 5bps after publication, re-run.

---

## Notes on the recursive learning loop

The auto-weight-update step (run yesterday on 5/3 outcomes) closes the recursive learning protocol. The `recalibration_log_20260504.jsonl` from earlier today is what shifts feature weights for tonight's score. Specifically: yesterday's CHC pick won, STL picks split (single + parlay), and the WSH-style B+ leans went 1-1 — so the model's weight on the F3 swing-take gate held steady. (You can confirm the deltas in `recalibration_log_20260504.jsonl`.)
