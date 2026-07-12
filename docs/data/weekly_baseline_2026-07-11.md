# Weekly Baseline Update — 2026-07-11
_Generated 2026-07-12T07:30:01Z · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_

## League pitching
- **14d** (from 2026-06-27): K% 22.10 · BB% 8.58 · HR/9 1.360 · ERA 4.37
- **30d** (from 2026-06-11): K% 22.24 · BB% 8.48 · HR/9 1.352 · ERA 4.35

## League hitting
- **14d**: AVG 0.247 · OBP 0.318 · SLG 0.417 · K% 22.10 · 1932 R
- **30d**: AVG 0.248 · OBP 0.318 · SLG 0.419 · K% 22.24 · 3830 R

## Season xwOBA (proxy)
- mean xwOBA **0.3001** across 591 qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_

## Drift vs frozen model priors
- **k_pct**: rolling-14d 22.10 vs prior 22.00 → Δ+0.10 (stable)
- **bb_pct**: rolling-14d 8.58 vs prior 8.50 → Δ+0.08 (stable)

_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._

## 14-day power ranking (run differential / game)
-  1. **Detroit Tigers** +2.15 R/G  (RS 74 / RA 46)
-  2. **Boston Red Sox** +1.92 R/G  (RS 63 / RA 38)
-  3. **Tampa Bay Rays** +1.43 R/G  (RS 66 / RA 46)
-  4. **Miami Marlins** +1.43 R/G  (RS 84 / RA 64)
-  5. **Chicago Cubs** +1.15 R/G  (RS 75 / RA 60)
-  6. **Los Angeles Dodgers** +0.93 R/G  (RS 79 / RA 66)
-  7. **Minnesota Twins** +0.92 R/G  (RS 63 / RA 51)
-  8. **St. Louis Cardinals** +0.80 R/G  (RS 67 / RA 55)
-  9. **Pittsburgh Pirates** +0.71 R/G  (RS 89 / RA 79)
- 10. **Arizona Diamondbacks** +0.71 R/G  (RS 64 / RA 54)
- 11. **Chicago White Sox** +0.64 R/G  (RS 59 / RA 50)
- 12. **Washington Nationals** +0.54 R/G  (RS 74 / RA 67)
- 13. **Milwaukee Brewers** +0.50 R/G  (RS 71 / RA 63)
- 14. **Cleveland Guardians** +0.36 R/G  (RS 59 / RA 54)
- 15. **Seattle Mariners** +0.31 R/G  (RS 50 / RA 46)
- 16. **Colorado Rockies** +0.27 R/G  (RS 88 / RA 84)
- 17. **Houston Astros** +0.00 R/G  (RS 72 / RA 72)
- 18. **Baltimore Orioles** -0.15 R/G  (RS 54 / RA 56)
- 19. **Atlanta Braves** -0.21 R/G  (RS 68 / RA 71)
- 20. **Texas Rangers** -0.31 R/G  (RS 63 / RA 67)
- 21. **Toronto Blue Jays** -0.38 R/G  (RS 51 / RA 56)
- 22. **Cincinnati Reds** -0.57 R/G  (RS 54 / RA 62)
- 23. **San Francisco Giants** -0.71 R/G  (RS 63 / RA 73)
- 24. **Los Angeles Angels** -0.85 R/G  (RS 51 / RA 62)
- 25. **Kansas City Royals** -0.92 R/G  (RS 58 / RA 70)
- 26. **New York Mets** -1.14 R/G  (RS 67 / RA 83)
- 27. **New York Yankees** -1.14 R/G  (RS 53 / RA 69)
- 28. **Philadelphia Phillies** -1.43 R/G  (RS 58 / RA 78)
- 29. **San Diego Padres** -3.00 R/G  (RS 58 / RA 103)
- 30. **Athletics** -3.85 R/G  (RS 37 / RA 87)
