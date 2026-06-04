# Weekly Baseline Update — 2026-06-03
_Generated 2026-06-04T08:37:25Z · rolling league baselines vs the model's frozen priors · READ-ONLY (changes nothing)_

## League pitching
- **14d** (from 2026-05-20): K% 21.98 · BB% 8.42 · HR/9 1.181 · ERA 4.12
- **30d** (from 2026-05-04): K% 21.96 · BB% 8.76 · HR/9 1.116 · ERA 4.06

## League hitting
- **14d**: AVG 0.244 · OBP 0.315 · SLG 0.403 · K% 21.98 · 1743 R
- **30d**: AVG 0.239 · OBP 0.313 · SLG 0.393 · K% 21.96 · 3593 R

## Season xwOBA (proxy)
- mean xwOBA **0.3045** across 529 qualified hitters — _unweighted (CSV has no PA col); season, not rolling; reads below PA-weighted LG_XWOBA by construction, so NOT a drift signal_

## Drift vs frozen model priors
- **k_pct**: rolling-14d 21.98 vs prior 22.00 → Δ-0.02 (stable)
- **bb_pct**: rolling-14d 8.42 vs prior 8.50 → Δ-0.08 (stable)

_Observational only. A persistent |Δ| beyond noise is a flag to revisit the priors in the POST-JAPAN retrain; it changes nothing now._

## 14-day power ranking (run differential / game)
-  1. **Los Angeles Dodgers** +3.08 R/G  (RS 75 / RA 35)
-  2. **Atlanta Braves** +1.85 R/G  (RS 69 / RA 45)
-  3. **New York Yankees** +1.83 R/G  (RS 64 / RA 42)
-  4. **Milwaukee Brewers** +1.69 R/G  (RS 60 / RA 38)
-  5. **Chicago White Sox** +1.57 R/G  (RS 79 / RA 57)
-  6. **Seattle Mariners** +1.54 R/G  (RS 62 / RA 42)
-  7. **Houston Astros** +1.38 R/G  (RS 71 / RA 53)
-  8. **Pittsburgh Pirates** +1.29 R/G  (RS 85 / RA 67)
-  9. **Los Angeles Angels** +1.14 R/G  (RS 82 / RA 66)
- 10. **Baltimore Orioles** +1.08 R/G  (RS 68 / RA 54)
- 11. **Boston Red Sox** +1.00 R/G  (RS 64 / RA 52)
- 12. **Washington Nationals** +0.64 R/G  (RS 60 / RA 51)
- 13. **Arizona Diamondbacks** +0.57 R/G  (RS 58 / RA 50)
- 14. **Toronto Blue Jays** +0.07 R/G  (RS 52 / RA 51)
- 15. **New York Mets** -0.21 R/G  (RS 53 / RA 56)
- 16. **Texas Rangers** -0.21 R/G  (RS 62 / RA 65)
- 17. **Detroit Tigers** -0.36 R/G  (RS 55 / RA 60)
- 18. **Philadelphia Phillies** -0.42 R/G  (RS 31 / RA 36)
- 19. **Cincinnati Reds** -0.67 R/G  (RS 52 / RA 60)
- 20. **Miami Marlins** -0.93 R/G  (RS 51 / RA 64)
- 21. **Cleveland Guardians** -1.00 R/G  (RS 41 / RA 54)
- 22. **Chicago Cubs** -1.23 R/G  (RS 43 / RA 59)
- 23. **San Diego Padres** -1.25 R/G  (RS 31 / RA 46)
- 24. **San Francisco Giants** -1.31 R/G  (RS 68 / RA 85)
- 25. **St. Louis Cardinals** -1.38 R/G  (RS 40 / RA 58)
- 26. **Minnesota Twins** -1.43 R/G  (RS 64 / RA 84)
- 27. **Kansas City Royals** -1.62 R/G  (RS 47 / RA 68)
- 28. **Athletics** -2.00 R/G  (RS 44 / RA 70)
- 29. **Colorado Rockies** -2.14 R/G  (RS 66 / RA 96)
- 30. **Tampa Bay Rays** -2.75 R/G  (RS 46 / RA 79)
