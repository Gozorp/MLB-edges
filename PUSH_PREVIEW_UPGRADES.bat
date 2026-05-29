@echo off
REM ===========================================================================
REM PUSH_PREVIEW_UPGRADES.bat   (run AFTER PUSH_GAME_PREVIEW.bat has pushed)
REM ---------------------------------------------------------------------------
REM Unifies the in-expander preview UI:
REM   1. "Top hitters" lists  -> Quant-Terminal tables (Name/Pos/OPS/HR/AVG/PA),
REM      OPS heatmapped (high = hot).
REM   2. "Probable starters"  -> table with DIRECTIONAL heat:
REM      ERA/WHIP low = hot, K/9 high = hot (maps color to PITCHER success).
REM   3. Bullpen narrative metrics get inline heat chips (ERA/WHIP/K9), keeping
REM      the leverage/fatigue narrative intact.
REM   4. SP K% chip in the projected-lineup header is heatmapped (high = hot).
REM
REM DEPENDS ON add_game_preview.py (the .gp-* CSS + theme). tools/
REM add_preview_upgrades.py guards for it and aborts if PUSH_GAME_PREVIEW
REM hasn't pushed yet.
REM
REM SAFE: Rule 4 safe-push (reset --hard origin/main first), Rule 3 gate
REM       (node --check the inline script before commit), Rule 12/13.
REM ===========================================================================
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

git fetch origin main
git reset --hard origin/main

REM --- apply upgrades (helper aborts if the base preview isn't on origin yet) ---
python tools\add_preview_upgrades.py
if errorlevel 1 ( echo upgrade step failed -- did PUSH_GAME_PREVIEW.bat push first? & pause & exit /b 1 )

REM --- structural gate: all upgrade markers present ---
python -c "s=open(r'docs/index.html',encoding='utf-8').read(); m=['/* GP-UPGRADES-FN','/* GP-UPGRADES-CSS','_gpBatterTable(preview.awayBatters)','_gpPitcherTable(preview.awayPitcher','_gpStat(r.era']; bad=[x for x in m if x not in s]; assert not bad, 'missing: '+str(bad); print('all upgrade markers present')"
if errorlevel 1 ( echo MARKER GATE FAILED & pause & exit /b 1 )

REM --- JS syntax gate: node --check the inline dashboard script ---
where node >nul 2>&1
if %errorlevel%==0 (
    python -c "import re; html=open(r'docs/index.html',encoding='utf-8').read(); inline=[b for a,b in re.findall(r'<script([^>]*)>(.*?)</script>', html, re.S) if 'src=' not in a]; open(r'%TEMP%\gpup.js','w',encoding='utf-8').write(inline[0] if inline else ''); print('inline scripts:', len(inline))"
    node --check "%TEMP%\gpup.js"
    if errorlevel 1 ( echo JS SYNTAX GATE FAILED -- not committing & pause & exit /b 1 )
    echo JS syntax OK
) else (
    echo node not on PATH -- skipping JS check ^(already validated upstream^)
)

git add docs\index.html tools\add_preview_upgrades.py PUSH_PREVIEW_UPGRADES.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "feat(dashboard): unify preview UI - hitter/starter tables + directional pitching heatmaps" -m "Top-hitters lists and probable-starters now render as Quant-Terminal tables matching the projected-lineup table. Heatmap is directional and maps to PITCHER success: ERA/WHIP low=hot, K%/K9 high=hot; OPS high=hot. Bullpen narrative keeps its leverage/fatigue text with inline heat chips on ERA/WHIP/K9. Via tools/add_preview_upgrades.py (idempotent, guarded on the base preview, backup). Rule 3 node-check'd, Rule 4 safe-push."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Expander UI unified: hitters + starters are tables, pitching metrics
    echo heatmapped to pitcher success ^(low ERA/WHIP + high K = red/hot^).
    echo Open a match under The Slate to see it.
) else (
    echo Nothing to commit -- already current on origin.
)
echo.
pause
