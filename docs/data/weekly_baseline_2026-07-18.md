# Weekly Baseline Update — 2026-07-18
_Generated 2026-07-19T07:30:00Z · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_

## League pitching
- **14d** (from 2026-07-04): K% 22.56 · BB% 8.62 · HR/9 1.306 · ERA 4.27
- **30d** (from 2026-06-18): K% 22.57 · BB% 8.51 · HR/9 1.295 · ERA 4.29

## League hitting
- **14d**: AVG 0.243 · OBP 0.316 · SLG 0.409 · K% 22.56 · 1431 R
- **30d**: AVG 0.245 · OBP 0.316 · SLG 0.412 · K% 22.57 · 3397 R

## Season xwOBA (proxy)
- mean xwOBA **0.3001** across 591 qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_

## Drift vs frozen model priors
- **k_pct**: rolling-14d 22.56 vs prior 22.00 → Δ+0.56 (stable)
- **bb_pct**: rolling-14d 8.62 vs prior 8.50 → Δ+0.12 (stable)

_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._

## 14-day power ranking (run differential / game)
-  1. **Boston Red Sox** +4.00 R/G  (RS 65 / RA 21)
-  2. **Detroit Tigers** +2.70 R/G  (RS 46 / RA 19)
-  3. **Pittsburgh Pirates** +2.70 R/G  (RS 69 / RA 42)
-  4. **Chicago White Sox** +1.90 R/G  (RS 48 / RA 29)
-  5. **Atlanta Braves** +1.73 R/G  (RS 73 / RA 54)
-  6. **Arizona Diamondbacks** +1.55 R/G  (RS 54 / RA 37)
-  7. **Minnesota Twins** +1.40 R/G  (RS 47 / RA 33)
-  8. **Baltimore Orioles** +1.40 R/G  (RS 48 / RA 34)
-  9. **San Francisco Giants** +0.82 R/G  (RS 53 / RA 44)
- 10. **Chicago Cubs** +0.60 R/G  (RS 43 / RA 37)
- 11. **Miami Marlins** +0.30 R/G  (RS 44 / RA 41)
- 12. **Milwaukee Brewers** +0.17 R/G  (RS 56 / RA 54)
- 13. **Kansas City Royals** +0.09 R/G  (RS 61 / RA 60)
- 14. **Cleveland Guardians** +0.00 R/G  (RS 36 / RA 36)
- 15. **New York Yankees** +0.00 R/G  (RS 41 / RA 41)
- 16. **Seattle Mariners** -0.20 R/G  (RS 39 / RA 41)
- 17. **Houston Astros** -0.20 R/G  (RS 52 / RA 54)
- 18. **Cincinnati Reds** -0.40 R/G  (RS 41 / RA 45)
- 19. **St. Louis Cardinals** -0.58 R/G  (RS 41 / RA 48)
- 20. **San Diego Padres** -0.64 R/G  (RS 43 / RA 50)
- 21. **Colorado Rockies** -0.82 R/G  (RS 46 / RA 55)
- 22. **Washington Nationals** -0.90 R/G  (RS 61 / RA 70)
- 23. **Philadelphia Phillies** -1.18 R/G  (RS 37 / RA 50)
- 24. **Los Angeles Angels** -1.40 R/G  (RS 38 / RA 52)
- 25. **Los Angeles Dodgers** -1.44 R/G  (RS 30 / RA 43)
- 26. **New York Mets** -1.45 R/G  (RS 54 / RA 70)
- 27. **Toronto Blue Jays** -1.50 R/G  (RS 41 / RA 56)
- 28. **Tampa Bay Rays** -1.67 R/G  (RS 46 / RA 66)
- 29. **Texas Rangers** -2.60 R/G  (RS 43 / RA 69)
- 30. **Athletics** -4.50 R/G  (RS 35 / RA 80)
