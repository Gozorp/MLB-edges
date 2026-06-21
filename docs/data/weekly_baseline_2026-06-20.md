# Weekly Baseline Update — 2026-06-20
_Generated 2026-06-21T07:30:01Z · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_

## League pitching
- **14d** (from 2026-06-06): K% 21.96 · BB% 8.62 · HR/9 1.413 · ERA 4.50
- **30d** (from 2026-05-21): K% 21.89 · BB% 8.58 · HR/9 1.312 · ERA 4.37

## League hitting
- **14d**: AVG 0.252 · OBP 0.325 · SLG 0.431 · K% 21.96 · 1830 R
- **30d**: AVG 0.249 · OBP 0.321 · SLG 0.419 · K% 21.89 · 3700 R

## Season xwOBA (proxy)
- mean xwOBA **0.3045** across 529 qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_

## Drift vs frozen model priors
- **k_pct**: rolling-14d 21.96 vs prior 22.00 → Δ-0.04 (stable)
- **bb_pct**: rolling-14d 8.62 vs prior 8.50 → Δ+0.12 (stable)

_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._

## 14-day power ranking (run differential / game)
-  1. **New York Yankees** +2.00 R/G  (RS 70 / RA 46)
-  2. **Milwaukee Brewers** +1.77 R/G  (RS 78 / RA 55)
-  3. **Miami Marlins** +1.77 R/G  (RS 66 / RA 43)
-  4. **Chicago Cubs** +1.38 R/G  (RS 67 / RA 49)
-  5. **Detroit Tigers** +1.25 R/G  (RS 54 / RA 39)
-  6. **Los Angeles Angels** +1.00 R/G  (RS 76 / RA 62)
-  7. **Philadelphia Phillies** +0.92 R/G  (RS 73 / RA 61)
-  8. **Washington Nationals** +0.77 R/G  (RS 68 / RA 58)
-  9. **Los Angeles Dodgers** +0.77 R/G  (RS 73 / RA 63)
- 10. **Kansas City Royals** +0.62 R/G  (RS 72 / RA 64)
- 11. **St. Louis Cardinals** +0.54 R/G  (RS 70 / RA 63)
- 12. **San Diego Padres** +0.46 R/G  (RS 58 / RA 52)
- 13. **Minnesota Twins** +0.31 R/G  (RS 83 / RA 79)
- 14. **Cincinnati Reds** -0.08 R/G  (RS 54 / RA 55)
- 15. **Boston Red Sox** -0.08 R/G  (RS 45 / RA 46)
- 16. **Colorado Rockies** -0.23 R/G  (RS 71 / RA 74)
- 17. **Tampa Bay Rays** -0.31 R/G  (RS 44 / RA 48)
- 18. **San Francisco Giants** -0.42 R/G  (RS 48 / RA 53)
- 19. **Houston Astros** -0.54 R/G  (RS 60 / RA 67)
- 20. **Toronto Blue Jays** -0.62 R/G  (RS 56 / RA 64)
- 21. **Baltimore Orioles** -0.64 R/G  (RS 56 / RA 65)
- 22. **Seattle Mariners** -0.79 R/G  (RS 53 / RA 64)
- 23. **Atlanta Braves** -0.91 R/G  (RS 38 / RA 48)
- 24. **Cleveland Guardians** -0.92 R/G  (RS 43 / RA 54)
- 25. **Chicago White Sox** -1.00 R/G  (RS 50 / RA 62)
- 26. **Athletics** -1.07 R/G  (RS 91 / RA 106)
- 27. **Arizona Diamondbacks** -1.08 R/G  (RS 52 / RA 66)
- 28. **New York Mets** -1.46 R/G  (RS 53 / RA 72)
- 29. **Texas Rangers** -1.69 R/G  (RS 53 / RA 75)
- 30. **Pittsburgh Pirates** -1.69 R/G  (RS 55 / RA 77)
