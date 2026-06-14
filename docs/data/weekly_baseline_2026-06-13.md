# Weekly Baseline Update — 2026-06-13
_Generated 2026-06-14T07:30:01Z · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_

## League pitching
- **14d** (from 2026-05-30): K% 21.99 · BB% 8.90 · HR/9 1.357 · ERA 4.64
- **30d** (from 2026-05-14): K% 21.84 · BB% 8.68 · HR/9 1.238 · ERA 4.30

## League hitting
- **14d**: AVG 0.252 · OBP 0.326 · SLG 0.427 · K% 21.99 · 1915 R
- **30d**: AVG 0.246 · OBP 0.319 · SLG 0.410 · K% 21.84 · 3762 R

## Season xwOBA (proxy)
- mean xwOBA **0.3045** across 529 qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_

## Drift vs frozen model priors
- **k_pct**: rolling-14d 21.99 vs prior 22.00 → Δ-0.01 (stable)
- **bb_pct**: rolling-14d 8.90 vs prior 8.50 → Δ+0.40 (stable)

_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._

## 14-day power ranking (run differential / game)
-  1. **Milwaukee Brewers** +2.07 R/G  (RS 102 / RA 73)
-  2. **Los Angeles Angels** +2.00 R/G  (RS 81 / RA 55)
-  3. **St. Louis Cardinals** +1.69 R/G  (RS 74 / RA 52)
-  4. **Detroit Tigers** +1.54 R/G  (RS 67 / RA 47)
-  5. **Los Angeles Dodgers** +1.50 R/G  (RS 80 / RA 59)
-  6. **Boston Red Sox** +1.33 R/G  (RS 61 / RA 45)
-  7. **Miami Marlins** +1.00 R/G  (RS 58 / RA 45)
-  8. **Baltimore Orioles** +0.93 R/G  (RS 81 / RA 68)
-  9. **Washington Nationals** +0.77 R/G  (RS 71 / RA 61)
- 10. **Chicago White Sox** +0.75 R/G  (RS 61 / RA 52)
- 11. **New York Mets** +0.69 R/G  (RS 57 / RA 48)
- 12. **New York Yankees** +0.58 R/G  (RS 62 / RA 55)
- 13. **Atlanta Braves** +0.50 R/G  (RS 51 / RA 45)
- 14. **Seattle Mariners** +0.29 R/G  (RS 63 / RA 59)
- 15. **Houston Astros** +0.23 R/G  (RS 71 / RA 68)
- 16. **Kansas City Royals** +0.14 R/G  (RS 72 / RA 70)
- 17. **Texas Rangers** +0.08 R/G  (RS 55 / RA 54)
- 18. **Philadelphia Phillies** +0.00 R/G  (RS 60 / RA 60)
- 19. **San Francisco Giants** -0.29 R/G  (RS 81 / RA 85)
- 20. **Chicago Cubs** -0.38 R/G  (RS 51 / RA 56)
- 21. **Athletics** -0.46 R/G  (RS 73 / RA 79)
- 22. **Pittsburgh Pirates** -0.62 R/G  (RS 75 / RA 83)
- 23. **San Diego Padres** -1.08 R/G  (RS 46 / RA 60)
- 24. **Toronto Blue Jays** -1.15 R/G  (RS 56 / RA 71)
- 25. **Cleveland Guardians** -1.31 R/G  (RS 45 / RA 62)
- 26. **Tampa Bay Rays** -1.85 R/G  (RS 46 / RA 70)
- 27. **Cincinnati Reds** -1.92 R/G  (RS 42 / RA 67)
- 28. **Minnesota Twins** -2.07 R/G  (RS 70 / RA 99)
- 29. **Colorado Rockies** -2.23 R/G  (RS 69 / RA 98)
- 30. **Arizona Diamondbacks** -2.50 R/G  (RS 34 / RA 69)
