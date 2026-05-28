@echo off
REM Patch weather.js to render forecast-at-first-pitch for pre-game rows
REM instead of current-weather. For live or finished games keep current.
REM Pulls game start times from MLB Stats API schedule, picks the matching
REM hourly slot from the Open-Meteo forecast.
REM
REM Visual change: pre-game chips get a small blue 'fcst' badge; expanded
REM HUD shows "FORECAST · first pitch 7:10 PM" instead of "current · 15m".
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM Preserve the patched weather.js through hard-reset.
copy /Y docs\js\weather.js docs\js\weather.js.fcst_patched >nul
if errorlevel 1 ( echo backup failed & pause & exit /b 1 )

git fetch origin main
git reset --hard origin/main

copy /Y docs\js\weather.js.fcst_patched docs\js\weather.js >nul
del docs\js\weather.js.fcst_patched

git add docs\js\weather.js
git add PUSH_WEATHER_FORECAST.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "feat(dashboard): weather chip shows forecast at first-pitch for pre-game rows" -m "Previously every chip showed CURRENT weather at the stadium. For a 7:10 PM first pitch checked at 11 AM that's wrong if rain at 11 clears by 7. Now the chip pulls each game's gameDate from MLB Stats API schedule (cached 5min), and picks the matching hourly forecast slot from Open-Meteo for any game starting > 30 min in the future. Live + finished games still use current." -m "Implementation: fetchStadiumWeatherFull pulls current + 48h hourly in one call (timezone=GMT to align with MLB gameDate UTC). extractWeatherAt(meteo, targetIso) returns forecast-at-hour if targetIso provided and within window, else current. targetIsoForGame returns the gameDate when start > now+30min, null otherwise. Chips with is_forecast=true get a blue 'fcst' badge and a tooltip noting the local first-pitch time. The HUD's bottom-right shifts to 'FORECAST · first pitch 7:10 PM'." -m "TEAM_ABBR_TO_ID + TEAM_ID_TO_ABBR mappings inlined (same as tools/luck_adjusted_probe.py). User chip unchanged - always current weather (you're not playing a game at a future time)."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Hard refresh dashboard ^(Ctrl+Shift+R^). Pre-game rows should show
    echo 'fcst' badge on the row chip and 'FORECAST first pitch 7:10 PM'
    echo in the expanded HUD. Live + finished games unchanged.
) else (
    echo no changes to commit
)
pause
