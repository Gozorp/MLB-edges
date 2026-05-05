# Full Audit — MLB Slate Tuesday, April 28, 2026

_Generated 2026-04-28 00:47_  · 11 games scored


## Slate summary

| matchup | pick | p_pick | fair | edge_pp | EV/$ | tier | stake× |
|---|---|---:|---:|---:|---:|---|---:|
| LAA @ CHW | **CHW** | 0.919 | 0.423 | +48.91 | +1.003 | SKIP | 0.0 |
| WSH @ NYM | **WSH** | 0.762 | 0.348 | +41.57 | +0.974 | SKIP | 0.0 |
| SEA @ MIN | **MIN** | 0.736 | 0.453 | +25.60 | +0.477 | SKIP | 0.0 |
| HOU @ BAL | **HOU** | 0.664 | 0.431 | +24.51 | +0.471 | SKIP | 0.0 |
| BOS @ TOR | **TOR** | 0.765 | 0.523 | +21.75 | +0.370 | SKIP | 0.0 |
| NYY @ TEX | **NYY** | 0.650 | 0.535 | +17.64 | +0.297 | GOLD | 0.0 |
| DET @ ATL | **ATL** | 0.721 | 0.534 | +15.82 | +0.261 | SKIP | 0.0 |
| TB @ CLE | **TB** | 0.585 | 0.438 | +15.12 | +0.267 | GOLD | 0.0 |
| CHC @ SD | **SD** | 0.550 | 0.471 | +3.05 | +0.016 | SKIP | 0.0 |
| SF @ PHI | **PHI** | 0.621 | 0.631 | -4.74 | -0.078 | GOLD | 0.0 |
| MIA @ LAD | **LAD** | 0.718 | 0.763 | -6.04 | -0.059 | PLATINUM | 0.3 |

## Bet sheet

**No plays clear all gates** (edge ∈ [4, 15]pp, model_prob ∈ [0.48, 0.72], fair_prob ≥ 0.42, tier with stake>0).

## Per-game cards


### TB @ CLE → **TB** (58.5%) · GOLD

**Headline numbers**

- p_model=0.4148 (home) · p_pick=0.5852 · f5_prob=0.5689 · fair_prob=0.4383
- edge_pp=+15.12pp · EV/$=+0.2674
- conviction tier **GOLD** · stake_mult=0.0
- signals: F3_swing_take_gap=1070.2
- suppression / notes: F5 suppressed (bp_n_pitches our=1434.0, opp=2070.0 < 3000)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=593 (F1 floor 600)
- away SP id: `—` · n_pitches=522 (F1 floor 600)
- lineup_wrcplus_gap=+5.115
- lineup_vs_sp_gap=+0.013
- lineup_hardhit_gap=+3.276

**Bullpen state (72h)**
- home bp pitches: 2070 (F5 floor 3000)
- away bp pitches: 1434 (F5 floor 3000)
- bullpen_siera_gap=+0.523
- bullpen_fatigue_gap=+0.050
- bullpen_xwoba_gap=+0.008

**Weather / park**
- 56°F · +1.3mph out · 277° wind · 80% RH · 0% precip · park_runs=0.980 · park_hr=0.947 · roof=0

**Gate trail (bet sheet)**
- PASS model_prob 0.585 in band
- PASS fair_prob 0.438 >= 0.42
- FAIL edge +15.12pp > 15pp (likely bad number)
- FAIL tier GOLD -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.014 | +0.4pp | — |
| SP_luck | -0.110 | -2.7pp | TB |
| Offense | -0.733 | -18.3pp | TB |
| Bullpen | -0.001 | -0.0pp | — |
| Park | +0.085 | +2.1pp | CLE |
| Ump_Catcher | +0.006 | +0.1pp | — |
| Defense | +0.255 | +6.4pp | CLE |
| Context | -0.046 | -1.1pp | TB |
| **net (ex-bias)** | **-0.530** | **-13.3pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| -0.626 | `team_batter_run_value_gap` | -20.580 | TB |
| +0.143 | `team_frv_gap` | +16.718 | CLE |
| -0.119 | `team_bbk_gap` | -0.057 | TB |
| -0.112 | `team_hardhit_gap` | +0.470 | TB |
| +0.112 | `team_oaa_gap` | +16.000 | CLE |

### HOU @ BAL → **HOU** (66.4%) · SKIP

**Headline numbers**

- p_model=0.3364 (home) · p_pick=0.6636 · f5_prob=0.6957 · fair_prob=0.4311
- edge_pp=+24.51pp · EV/$=+0.4707
- conviction tier **SKIP** · stake_mult=0.0

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=546 (F1 floor 600)
- away SP id: `—` · n_pitches=269 (F1 floor 600)
- lineup_wrcplus_gap=-6.364
- lineup_vs_sp_gap=-0.026
- lineup_hardhit_gap=+0.030

**Bullpen state (72h)**
- home bp pitches: 1981 (F5 floor 3000)
- away bp pitches: 2144 (F5 floor 3000)
- bullpen_siera_gap=+0.287
- bullpen_fatigue_gap=+0.253
- bullpen_xwoba_gap=+0.007

**Weather / park**
- 64°F · -7.0mph out · 131° wind · 34% RH · 2% precip · park_runs=1.020 · park_hr=1.071 · roof=0

**Gate trail (bet sheet)**
- PASS model_prob 0.664 in band
- PASS fair_prob 0.431 >= 0.42
- FAIL edge +24.51pp > 15pp (likely bad number)
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.067 | +1.7pp | BAL |
| SP_luck | +0.016 | +0.4pp | — |
| Offense | -0.796 | -19.9pp | HOU |
| Bullpen | +0.127 | +3.2pp | BAL |
| Park | -0.004 | -0.1pp | — |
| Ump_Catcher | -0.037 | -0.9pp | HOU |
| Defense | -0.103 | -2.6pp | HOU |
| Context | -0.086 | -2.1pp | HOU |
| **net (ex-bias)** | **-0.815** | **-20.4pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| -0.641 | `team_batter_run_value_gap` | -24.811 | HOU |
| +0.126 | `bullpen_bb_pct_gap` | +2.583 | BAL |
| -0.114 | `lineup_vs_sp_gap` | -0.026 | HOU |
| -0.084 | `team_bbk_gap` | -2.459 | HOU |
| +0.071 | `home_sp_luck` | +2.140 | BAL |

### SF @ PHI → **PHI** (62.1%) · GOLD

**Headline numbers**

- p_model=0.6206 (home) · p_pick=0.6206 · f5_prob=0.6669 · fair_prob=0.6309
- edge_pp=-4.74pp · EV/$=-0.0781
- conviction tier **GOLD** · stake_mult=0.0
- signals: F3_swing_take_gap=944.5
- suppression / notes: F1 suppressed (n_pitches home=570.0, away=445.0 < 600)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=570 (F1 floor 600)
- away SP id: `—` · n_pitches=445 (F1 floor 600)
- lineup_wrcplus_gap=-1.362
- lineup_vs_sp_gap=+0.018
- lineup_hardhit_gap=+6.307

**Bullpen state (72h)**
- home bp pitches: 1821 (F5 floor 3000)
- away bp pitches: 1767 (F5 floor 3000)
- bullpen_siera_gap=+0.150
- bullpen_fatigue_gap=+0.073
- bullpen_xwoba_gap=+0.003

**Weather / park**
- 63°F · -4.2mph out · 115° wind · 33% RH · 3% precip · park_runs=1.010 · park_hr=1.069 · roof=0

**Gate trail (bet sheet)**
- PASS model_prob 0.621 in band
- PASS fair_prob 0.631 >= 0.42
- FAIL edge -4.74pp < 4pp
- FAIL tier GOLD -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.059 | +1.5pp | PHI |
| SP_luck | +0.013 | +0.3pp | — |
| Offense | +0.341 | +8.5pp | PHI |
| Bullpen | -0.027 | -0.7pp | SF |
| Park | +0.027 | +0.7pp | PHI |
| Ump_Catcher | +0.026 | +0.7pp | PHI |
| Defense | -0.055 | -1.4pp | SF |
| Context | -0.076 | -1.9pp | SF |
| **net (ex-bias)** | **+0.309** | **+7.7pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.167 | `team_batter_run_value_gap` | +4.376 | PHI |
| -0.088 | `sp_ttop3_penalty_gap` | n/a | SF |
| +0.082 | `team_blast_swing_gap` | +0.011 | PHI |
| -0.076 | `team_bbk_gap` | -0.021 | SF |
| +0.063 | `park_hr_factor` | +1.069 | PHI |

### BOS @ TOR → **TOR** (76.5%) · SKIP

**Headline numbers**

- p_model=0.7648 (home) · p_pick=0.7648 · f5_prob=0.5011 · fair_prob=0.5226
- edge_pp=+21.75pp · EV/$=+0.3702
- conviction tier **SKIP** · stake_mult=0.0

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=n/a
- away SP id: `—` · n_pitches=n/a
- lineup_wrcplus_gap=-2.317
- lineup_vs_sp_gap=+0.002
- lineup_hardhit_gap=+0.551

**Bullpen state (72h)**
- home bp pitches: 1695 (F5 floor 3000)
- away bp pitches: 2046 (F5 floor 3000)
- bullpen_siera_gap=+0.348
- bullpen_fatigue_gap=+0.070
- bullpen_xwoba_gap=+0.010

**Weather / park**
- 59°F · -0.5mph out · 267° wind · 82% RH · 2% precip · park_runs=1.000 · park_hr=1.024 · roof=1

**News override**
- rationale: home bullpen short -> 1.5pp toward away
- rules: bullpen_short_home
- model_prob delta: -0.015
- bullpen short (home)

**Gate trail (bet sheet)**
- FAIL model_prob 0.765 outside [0.48,0.72]
- PASS fair_prob 0.523 >= 0.42
- FAIL edge +21.75pp > 15pp (likely bad number)
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.021 | +0.5pp | TOR |
| SP_luck | +0.318 | +7.9pp | TOR |
| Offense | +0.664 | +16.6pp | TOR |
| Bullpen | -0.005 | -0.1pp | — |
| Park | +0.018 | +0.5pp | — |
| Ump_Catcher | +0.065 | +1.6pp | TOR |
| Defense | -0.105 | -2.6pp | BOS |
| Context | +0.055 | +1.4pp | TOR |
| **net (ex-bias)** | **+1.030** | **+25.8pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.363 | `away_sp_luck` | n/a | TOR |
| +0.179 | `team_whiff_rate_gap` | +0.049 | TOR |
| +0.164 | `team_batter_run_value_gap` | +18.783 | TOR |
| +0.148 | `team_bbk_gap` | +1.457 | TOR |
| +0.099 | `team_hardhit_gap` | -0.604 | TOR |

### WSH @ NYM → **WSH** (76.2%) · SKIP

**Headline numbers**

- p_model=0.2383 (home) · p_pick=0.7617 · f5_prob=0.5600 · fair_prob=0.3480
- edge_pp=+41.57pp · EV/$=+0.9742
- conviction tier **SKIP** · stake_mult=0.0
- suppression / notes: F1 negative-veto suppressed (small sample: home=389.0, away=437.0) | F5 suppressed (bp_n_pitches our=2151.0, opp=1876.0 < 3000)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=437 (F1 floor 600)
- away SP id: `—` · n_pitches=389 (F1 floor 600)
- lineup_wrcplus_gap=-17.205
- lineup_vs_sp_gap=-0.040
- lineup_hardhit_gap=+1.058

**Bullpen state (72h)**
- home bp pitches: 1876 (F5 floor 3000)
- away bp pitches: 2151 (F5 floor 3000)
- bullpen_siera_gap=+0.408
- bullpen_fatigue_gap=+0.147
- bullpen_xwoba_gap=+0.011

**Weather / park**
- 53°F · -4.7mph out · 131° wind · 62% RH · 2% precip · park_runs=0.950 · park_hr=0.869 · roof=0

**Gate trail (bet sheet)**
- FAIL model_prob 0.762 outside [0.48,0.72]
- FAIL fair_prob 0.348 < 0.42
- FAIL edge +41.57pp > 15pp (likely bad number)
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.020 | +0.5pp | NYM |
| SP_luck | -0.095 | -2.4pp | WSH |
| Offense | -1.198 | -30.0pp | WSH |
| Bullpen | +0.000 | +0.0pp | — |
| Park | -0.246 | -6.1pp | WSH |
| Ump_Catcher | +0.033 | +0.8pp | NYM |
| Defense | +0.113 | +2.8pp | NYM |
| Context | +0.002 | +0.1pp | — |
| **net (ex-bias)** | **-1.371** | **-34.3pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| -0.590 | `team_batter_run_value_gap` | -41.279 | WSH |
| -0.282 | `park_hr_factor` | +0.869 | WSH |
| -0.190 | `lineup_wrcplus_gap` | -17.205 | WSH |
| -0.169 | `team_bbk_gap` | -0.507 | WSH |
| -0.117 | `lineup_vs_sp_gap` | -0.040 | WSH |

### DET @ ATL → **ATL** (72.1%) · SKIP

**Headline numbers**

- p_model=0.7208 (home) · p_pick=0.7208 · f5_prob=0.3168 · fair_prob=0.5345
- edge_pp=+15.82pp · EV/$=+0.2606
- conviction tier **SKIP** · stake_mult=0.0
- suppression / notes: F1 negative-veto suppressed (small sample: home=354.0, away=491.0)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=354 (F1 floor 600)
- away SP id: `—` · n_pitches=491 (F1 floor 600)
- lineup_wrcplus_gap=+3.228
- lineup_vs_sp_gap=+0.013
- lineup_hardhit_gap=+0.949

**Bullpen state (72h)**
- home bp pitches: 1571 (F5 floor 3000)
- away bp pitches: 1818 (F5 floor 3000)
- bullpen_siera_gap=+0.299
- bullpen_fatigue_gap=-0.263
- bullpen_xwoba_gap=+0.008

**Weather / park**
- 74°F · -5.4mph out · 229° wind · 65% RH · 6% precip · park_runs=1.010 · park_hr=1.045 · roof=0

**News override**
- rationale: home bullpen short -> 1.5pp toward away
- rules: bullpen_short_home
- model_prob delta: -0.015
- bullpen short (home)

**Gate trail (bet sheet)**
- FAIL model_prob 0.721 outside [0.48,0.72]
- PASS fair_prob 0.534 >= 0.42
- FAIL edge +15.82pp > 15pp (likely bad number)
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | -0.063 | -1.6pp | DET |
| SP_luck | -0.030 | -0.8pp | DET |
| Offense | +0.264 | +6.6pp | ATL |
| Bullpen | +0.145 | +3.6pp | ATL |
| Park | -0.041 | -1.0pp | DET |
| Ump_Catcher | +0.017 | +0.4pp | — |
| Defense | +0.309 | +7.7pp | ATL |
| Context | +0.152 | +3.8pp | ATL |
| **net (ex-bias)** | **+0.753** | **+18.8pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.188 | `team_frv_gap` | +15.244 | ATL |
| +0.174 | `sp_sample_reliability` | +0.236 | ATL |
| +0.121 | `team_oaa_gap` | +23.000 | ATL |
| +0.108 | `bullpen_bb_pct_gap` | +1.504 | ATL |
| +0.076 | `team_wrcplus_gap` | +7.556 | ATL |

### SEA @ MIN → **MIN** (73.6%) · SKIP

**Headline numbers**

- p_model=0.7357 (home) · p_pick=0.7357 · f5_prob=0.6664 · fair_prob=0.4525
- edge_pp=+25.60pp · EV/$=+0.4772
- conviction tier **SKIP** · stake_mult=0.0
- suppression / notes: F1 suppressed (n_pitches home=599.0, away=550.0 < 600)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=599 (F1 floor 600)
- away SP id: `—` · n_pitches=550 (F1 floor 600)
- lineup_wrcplus_gap=+3.807
- lineup_vs_sp_gap=-0.029
- lineup_hardhit_gap=+1.094

**Bullpen state (72h)**
- home bp pitches: 1829 (F5 floor 3000)
- away bp pitches: 1843 (F5 floor 3000)
- bullpen_siera_gap=+0.136
- bullpen_fatigue_gap=-0.140
- bullpen_xwoba_gap=+0.003

**Weather / park**
- 50°F · +5.8mph out · 304° wind · 57% RH · 1% precip · park_runs=1.000 · park_hr=0.975 · roof=0

**Gate trail (bet sheet)**
- FAIL model_prob 0.736 outside [0.48,0.72]
- PASS fair_prob 0.453 >= 0.42
- FAIL edge +25.60pp > 15pp (likely bad number)
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.061 | +1.5pp | MIN |
| SP_luck | +0.036 | +0.9pp | MIN |
| Offense | +0.683 | +17.1pp | MIN |
| Bullpen | -0.023 | -0.6pp | SEA |
| Park | +0.019 | +0.5pp | — |
| Ump_Catcher | +0.037 | +0.9pp | MIN |
| Defense | +0.088 | +2.2pp | MIN |
| Context | -0.047 | -1.2pp | SEA |
| **net (ex-bias)** | **+0.854** | **+21.4pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.190 | `team_whiff_rate_gap` | +0.053 | MIN |
| +0.170 | `team_batter_run_value_gap` | +11.513 | MIN |
| +0.137 | `team_bbk_gap` | +1.821 | MIN |
| +0.095 | `away_catcher_penalty` | +0.000 | MIN |
| +0.092 | `team_blast_swing_gap` | +0.011 | MIN |

### LAA @ CHW → **CHW** (91.9%) · SKIP

**Headline numbers**

- p_model=0.9192 (home) · p_pick=0.9192 · f5_prob=0.5097 · fair_prob=0.4235
- edge_pp=+48.91pp · EV/$=+1.0032
- conviction tier **SKIP** · stake_mult=0.0
- suppression / notes: F1 negative-veto suppressed (small sample: home=445.0, away=623.0)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=445 (F1 floor 600)
- away SP id: `—` · n_pitches=623 (F1 floor 600)

**Bullpen state (72h)**
- home bp pitches: 0 (F5 floor 3000)
- away bp pitches: 1988 (F5 floor 3000)
- bullpen_fatigue_gap=-0.627

**Weather / park**
- 58°F · +1.8mph out · 290° wind · 70% RH · 2% precip · park_runs=1.030 · park_hr=1.119 · roof=0

**News override**
- rationale: home bullpen short -> 1.5pp toward away | away bullpen short -> 1.5pp toward home
- rules: bullpen_short_home;bullpen_short_away
- bullpen short (home)
- bullpen short (away)

**Gate trail (bet sheet)**
- FAIL model_prob 0.919 outside [0.48,0.72]
- PASS fair_prob 0.423 >= 0.42
- FAIL edge +48.91pp > 15pp (likely bad number)
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.016 | +0.4pp | — |
| SP_luck | +0.279 | +7.0pp | CHW |
| Offense | +0.848 | +21.2pp | CHW |
| Bullpen | +0.911 | +22.8pp | CHW |
| Park | +0.013 | +0.3pp | — |
| Ump_Catcher | +0.051 | +1.3pp | CHW |
| Defense | +0.135 | +3.4pp | CHW |
| Context | -0.030 | -0.8pp | LAA |
| **net (ex-bias)** | **+2.224** | **+55.6pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.468 | `lineup_vs_sp_gap` | n/a | CHW |
| +0.288 | `bullpen_siera_gap` | n/a | CHW |
| +0.225 | `team_batter_run_value_gap` | +12.644 | CHW |
| +0.219 | `away_sp_luck` | +0.795 | CHW |
| +0.204 | `lineup_wrcplus_gap` | n/a | CHW |

### NYY @ TEX → **NYY** (65.0%) · GOLD

**Headline numbers**

- p_model=0.3502 (home) · p_pick=0.6498 · f5_prob=0.4766 · fair_prob=0.5346
- edge_pp=+17.64pp · EV/$=+0.2968
- conviction tier **GOLD** · stake_mult=0.0
- signals: F2_xwoba_gap=0.024
- suppression / notes: F1 suppressed (n_pitches home=569.0, away=430.0 < 600)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=430 (F1 floor 600)
- away SP id: `—` · n_pitches=569 (F1 floor 600)
- lineup_wrcplus_gap=-5.846
- lineup_vs_sp_gap=-0.022
- lineup_hardhit_gap=-2.475

**Bullpen state (72h)**
- home bp pitches: 1752 (F5 floor 3000)
- away bp pitches: 1764 (F5 floor 3000)
- bullpen_siera_gap=-0.161
- bullpen_fatigue_gap=-0.053
- bullpen_xwoba_gap=-0.004

**Weather / park**
- 82°F · +7.4mph out · 15° wind · 70% RH · 10% precip · park_runs=0.970 · park_hr=1.057 · roof=1

**News override**
- rationale: home bullpen short -> 1.5pp toward away
- rules: bullpen_short_home
- model_prob delta: -0.015
- bullpen short (home)

**Gate trail (bet sheet)**
- PASS model_prob 0.650 in band
- PASS fair_prob 0.535 >= 0.42
- FAIL edge +17.64pp > 15pp (likely bad number)
- FAIL tier GOLD -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.005 | +0.1pp | — |
| SP_luck | +0.020 | +0.5pp | TEX |
| Offense | -0.863 | -21.6pp | NYY |
| Bullpen | +0.005 | +0.1pp | — |
| Park | +0.250 | +6.3pp | TEX |
| Ump_Catcher | -0.051 | -1.3pp | NYY |
| Defense | -0.117 | -2.9pp | NYY |
| Context | -0.024 | -0.6pp | NYY |
| **net (ex-bias)** | **-0.774** | **-19.3pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| -0.608 | `team_batter_run_value_gap` | -29.534 | NYY |
| +0.196 | `wind_dir_park` | +15.000 | TEX |
| +0.141 | `team_woba_gap` | -0.024 | TEX |
| -0.128 | `team_wrcplus_gap` | -11.005 | NYY |
| -0.118 | `lineup_vs_sp_gap` | -0.022 | NYY |

### CHC @ SD → **SD** (55.0%) · SKIP

**Headline numbers**

- p_model=0.5497 (home) · p_pick=0.5497 · f5_prob=0.5132 · fair_prob=0.4711
- edge_pp=+3.05pp · EV/$=+0.0158
- conviction tier **SKIP** · stake_mult=0.0
- suppression / notes: F1 negative-veto suppressed (small sample: home=450.0, away=532.0) | F5 suppressed (bp_n_pitches our=1901.0, opp=1723.0 < 3000)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=450 (F1 floor 600)
- away SP id: `—` · n_pitches=532 (F1 floor 600)
- lineup_wrcplus_gap=-8.251
- lineup_vs_sp_gap=+0.003
- lineup_hardhit_gap=+2.281

**Bullpen state (72h)**
- home bp pitches: 1901 (F5 floor 3000)
- away bp pitches: 1723 (F5 floor 3000)
- bullpen_siera_gap=+0.722
- bullpen_fatigue_gap=+0.230
- bullpen_xwoba_gap=+0.015

**Weather / park**
- 65°F · +6.0mph out · 314° wind · 62% RH · 0% precip · park_runs=0.950 · park_hr=0.945 · roof=0

**Gate trail (bet sheet)**
- PASS model_prob 0.550 in band
- PASS fair_prob 0.471 >= 0.42
- FAIL edge +3.05pp < 4pp
- FAIL tier SKIP -> stake_mult=0

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.004 | +0.1pp | — |
| SP_luck | +0.298 | +7.5pp | SD |
| Offense | -0.311 | -7.8pp | CHC |
| Bullpen | +0.027 | +0.7pp | SD |
| Park | +0.206 | +5.1pp | SD |
| Ump_Catcher | -0.027 | -0.7pp | CHC |
| Defense | -0.119 | -3.0pp | CHC |
| Context | -0.081 | -2.0pp | CHC |
| **net (ex-bias)** | **-0.003** | **-0.1pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.251 | `away_sp_luck` | +3.043 | SD |
| -0.181 | `team_wrcplus_gap` | -18.774 | CHC |
| -0.132 | `team_bbk_gap` | -1.885 | CHC |
| -0.127 | `lineup_wrcplus_gap` | -8.251 | CHC |
| +0.116 | `park_hr_factor` | +0.945 | SD |

### MIA @ LAD → **LAD** (71.8%) · PLATINUM

**Headline numbers**

- p_model=0.7181 (home) · p_pick=0.7181 · f5_prob=0.6658 · fair_prob=0.7628
- edge_pp=-6.04pp · EV/$=-0.0595
- conviction tier **PLATINUM** · stake_mult=0.3
- signals: F2_xwoba_gap=0.035, F3_swing_take_gap=6604.5
- suppression / notes: F1 suppressed (n_pitches home=455.0, away=464.0 < 600)

**Probable pitchers / lineups**
- home SP id: `—` · n_pitches=455 (F1 floor 600)
- away SP id: `—` · n_pitches=464 (F1 floor 600)
- lineup_wrcplus_gap=+18.790
- lineup_vs_sp_gap=+0.027
- lineup_hardhit_gap=+1.302

**Bullpen state (72h)**
- home bp pitches: 1609 (F5 floor 3000)
- away bp pitches: 1737 (F5 floor 3000)
- bullpen_siera_gap=-0.070
- bullpen_fatigue_gap=+0.013
- bullpen_xwoba_gap=-0.001

**Weather / park**
- 62°F · -2.5mph out · 249° wind · 60% RH · 0% precip · park_runs=0.980 · park_hr=1.019 · roof=0

**Gate trail (bet sheet)**
- PASS model_prob 0.718 in band
- PASS fair_prob 0.763 >= 0.42
- FAIL edge -6.04pp < 4pp
- PASS tier PLATINUM -> stake_mult=0.3

**SHAP feature-family contributions** (positive = favors home)
| family | logit | pp@.5 | direction |
|---|---:|---:|---|
| SP_matchup | +0.066 | +1.6pp | LAD |
| SP_luck | +0.038 | +1.0pp | LAD |
| Offense | +0.478 | +12.0pp | LAD |
| Bullpen | +0.032 | +0.8pp | LAD |
| Park | -0.041 | -1.0pp | MIA |
| Ump_Catcher | +0.012 | +0.3pp | — |
| Defense | +0.120 | +3.0pp | LAD |
| Context | +0.033 | +0.8pp | LAD |
| **net (ex-bias)** | **+0.738** | **+18.5pp** | bias=+0.131 |

**Top 5 individual drivers** (|logit| desc)
| logit | feature | raw | favors |
|---:|---|---:|---|
| +0.200 | `team_batter_run_value_gap` | +20.845 | LAD |
| +0.191 | `lineup_wrcplus_gap` | +18.790 | LAD |
| -0.172 | `team_woba_gap` | +0.035 | MIA |
| +0.158 | `team_wrcplus_gap` | +15.426 | LAD |
| +0.132 | `team_frv_gap` | +11.126 | LAD |

## Gate attrition

| gate result | n |
|---|---:|
| model_prob_out_of_band | 5 |
| fair_too_low | 0 |
| edge_too_small | 3 |
| edge_too_big | 3 |
| tier_no_stake | 0 |
| BET | 0 |

## Freshness sanity

- Savant categories with today's mtime: **1/36**
- Bat-tracking latest: `bat_tracking_2026_20260428.csv` (2026-04-28 00:20)
- B-R boxes for 2026-04-27: **0**
- B-R standings for 2026-04-28: **0**

_Stale Savant categories (mtime before today):_
  - abs-challenges/abs-challenges_20260427.csv  mtime=2026-04-27T22:36:37
  - active-spin/active-spin_20260427.csv  mtime=2026-04-27T22:36:38
  - arm-strength/arm-strength_20260427.csv  mtime=2026-04-27T22:36:39
  - baserunning/baserunning_20260427.csv  mtime=2026-04-27T22:36:40
  - baserunning-run-value/baserunning-run-value_20260427.csv  mtime=2026-04-27T22:36:41
  - basestealing-run-value/basestealing-run-value_20260427.csv  mtime=2026-04-27T22:36:42
  - bat-tracking/bat-tracking_20260427.csv  mtime=2026-04-27T22:36:44
  - bat-tracking-swing-path/bat-tracking-swing-path_20260427.csv  mtime=2026-04-27T22:36:45
  - batted-ball/batted-ball_20260427.csv  mtime=2026-04-27T22:36:46
  - catch-probability/catch-probability_20260427.csv  mtime=2026-04-27T22:36:47
