@echo off
REM Ship the live weather HUD: stadium_coords.json + weather.js + 1-line
REM index.html patch (script tag before </body>). Self-bootstrapping JS
REM auto-attaches chips to each Slate row via DOM scan + MutationObserver,
REM no renderSlate refactor needed.
REM
REM Same recovery pattern as PUSH_POSTGAME_PENDING.bat: back up the patched
REM index.html, hard-reset to clean origin (clears any null-sha1 cruft from
REM the local index), restore backup, stage specific files only, push.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM Preserve the patched index.html through the hard-reset.
copy /Y docs\index.html docs\index.html.weather_patched >nul
if errorlevel 1 ( echo backup failed & pause & exit /b 1 )

REM Hard-sync to clean origin. Untracked files (weather.js, stadium_coords.json,
REM the backup, this bat) survive the reset.
git fetch origin main
git reset --hard origin/main

REM Restore the patched index.html.
copy /Y docs\index.html.weather_patched docs\index.html >nul
del docs\index.html.weather_patched
del docs\index.html.pre-weather-bak 2>nul

REM Stage only the weather artifacts.
git add docs\index.html
git add docs\data\stadium_coords.json
git add docs\js\weather.js
git add PUSH_WEATHER.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "feat(dashboard): live weather HUD per stadium + user-location chip" -m "Adds a self-bootstrapping weather component that attaches inline chips to each Slate row and a full HUD inside each detail panel, plus a user-location chip in the header. Open-Meteo as the data source (free, no API key, no rate-limit headaches). IP-based geolocation via ipapi.co for the user chip (best-effort, fails silent)." -m "Files: docs/data/stadium_coords.json (lat/lon + roof flags per team abbrev; OAK -> Sutter Health, TB indoor=true, 7 retractables flagged), docs/js/weather.js (Open-Meteo fetch with 15-min cache, WMO code -> SVG icon mapping, C/F toggle persisted to localStorage defaulting to F, MutationObserver to catch re-renders, refresh interval). docs/index.html got a single one-line addition: <script defer src=js/weather.js> before </body>. renderSlate untouched." -m "Indoor handling per user spec: Tropicana flagged is_indoor=true, gets a muted INDOOR chip with home-icon SVG (weather not a factor). Retractables (HOU/AZ/MIL/MIA/SEA/TOR/TEX) show ambient outdoor weather with a small roof badge. Severity-2 codes (rain/snow) get a dimmer border; severity-3 (heavy rain/storms) get an orange-ish accent so blowout weather is visually obvious in the table."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Hard refresh dashboard ^(Ctrl+Shift+R^). Each Slate row should show
    echo a weather chip beside the matchup label; click into a row for the
    echo full HUD with wind arrow + precip percentage.
) else (
    echo no changes to commit
)
pause
