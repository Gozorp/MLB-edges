# Weekly Baseline Update — 2026-06-06
_Generated 2026-06-07T07:30:00Z · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_

## League pitching
- **14d** (from 2026-05-23): K% 21.74 · BB% 8.62 · HR/9 1.263 · ERA 4.36
- **30d** (from 2026-05-07): K% 21.92 · BB% 8.76 · HR/9 1.139 · ERA 4.11

## League hitting
- **14d**: AVG 0.247 · OBP 0.319 · SLG 0.413 · K% 21.74 · 1836 R
- **30d**: AVG 0.240 · OBP 0.314 · SLG 0.395 · K% 21.92 · 3608 R

## Season xwOBA (proxy)
- mean xwOBA **0.3045** across 529 qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_

## Drift vs frozen model priors
- **k_pct**: rolling-14d 21.74 vs prior 22.00 → Δ-0.26 (stable)
- **bb_pct**: rolling-14d 8.62 vs prior 8.50 → Δ+0.12 (stable)

_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._

## 14-day power ranking (run differential / game)
-  1. **Los Angeles Dodgers** +3.36 R/G  (RS 82 / RA 35)
-  2. **New York Yankees** +2.36 R/G  (RS 66 / RA 40)
-  3. **Houston Astros** +2.14 R/G  (RS 85 / RA 55)
-  4. **Baltimore Orioles** +1.93 R/G  (RS 83 / RA 56)
-  5. **Washington Nationals** +1.92 R/G  (RS 67 / RA 42)
-  6. **Chicago White Sox** +1.36 R/G  (RS 78 / RA 59)
-  7. **Seattle Mariners** +1.31 R/G  (RS 62 / RA 45)
-  8. **Milwaukee Brewers** +1.29 R/G  (RS 75 / RA 57)
-  9. **Atlanta Braves** +0.77 R/G  (RS 60 / RA 50)
- 10. **Boston Red Sox** +0.75 R/G  (RS 61 / RA 52)
- 11. **Pittsburgh Pirates** +0.64 R/G  (RS 81 / RA 72)
- 12. **San Francisco Giants** +0.57 R/G  (RS 93 / RA 85)
- 13. **Los Angeles Angels** +0.54 R/G  (RS 68 / RA 61)
- 14. **New York Mets** +0.38 R/G  (RS 53 / RA 48)
- 15. **Philadelphia Phillies** +0.15 R/G  (RS 44 / RA 42)
- 16. **Detroit Tigers** +0.08 R/G  (RS 55 / RA 54)
- 17. **St. Louis Cardinals** +0.08 R/G  (RS 54 / RA 53)
- 18. **Miami Marlins** -0.38 R/G  (RS 49 / RA 54)
- 19. **Texas Rangers** -0.43 R/G  (RS 54 / RA 60)
- 20. **Toronto Blue Jays** -0.64 R/G  (RS 58 / RA 67)
- 21. **Arizona Diamondbacks** -0.86 R/G  (RS 53 / RA 65)
- 22. **Cleveland Guardians** -1.00 R/G  (RS 43 / RA 56)
- 23. **Kansas City Royals** -1.21 R/G  (RS 58 / RA 75)
- 24. **Chicago Cubs** -1.57 R/G  (RS 54 / RA 76)
- 25. **San Diego Padres** -1.62 R/G  (RS 31 / RA 52)
- 26. **Cincinnati Reds** -1.62 R/G  (RS 51 / RA 72)
- 27. **Minnesota Twins** -1.73 R/G  (RS 65 / RA 91)
- 28. **Tampa Bay Rays** -2.67 R/G  (RS 46 / RA 78)
- 29. **Colorado Rockies** -2.85 R/G  (RS 66 / RA 103)
- 30. **Athletics** -3.08 R/G  (RS 41 / RA 81)
