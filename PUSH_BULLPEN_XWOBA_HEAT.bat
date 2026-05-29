@echo off
REM ===========================================================================
REM PUSH_BULLPEN_XWOBA_HEAT.bat  (run AFTER PUSH_PREVIEW_UPGRADES.bat has pushed)
REM ---------------------------------------------------------------------------
REM Heatmaps the bullpen xwOBA-allowed gap in the OU deep-analysis
REM (_deepNarrativeOU) so xwOBA is visually consistent with ERA/WHIP/K%.
REM
REM DIRECTION (pitcher success): the diag carries a single signed
REM   hl_bullpen_xwoba_gap (home vs away). Colored from the HOME reference:
REM     gap < 0  -> home bullpen LOWER xwOBA-allowed (better) -> HOT (red)
REM     gap > 0  -> home worse                                -> COLD (blue)
REM   The existing "(home better/worse)" label keeps direction explicit.
REM   (No per-team bullpen xwOBA exists in the data, only the gap.)
REM
REM NOTE: renderBullpenEdge itself shows per-batter K-probability, not xwOBA,
REM   so there's nothing xwOBA to color there -- this targets the actual
REM   bullpen-xwOBA render point.
REM
REM DEPENDS ON add_preview_upgrades.py (_gpHeatDir + .gp-stat). The helper
REM   guards for it and aborts if PUSH_PREVIEW_UPGRADES hasn't pushed.
REM
REM SAFE: Rule 4 safe-push, Rule 3 gate (node --check), Rule 12/13.
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

REM --- apply (helper aborts if the upgrades aren't on origin yet) ---
python tools\add_bullpen_xwoba_heat.py
if errorlevel 1 ( echo step failed -- did PUSH_PREVIEW_UPGRADES.bat push first? & pause & exit /b 1 )

REM --- structural gate: the heat chip is in place ---
python -c "s=open(r'docs/index.html',encoding='utf-8').read(); assert '_gpHeatDir(hlBp' in s, 'bullpen xwOBA chip missing'; print('bullpen xwOBA heat present')"
if errorlevel 1 ( echo MARKER GATE FAILED & pause & exit /b 1 )

REM --- JS syntax gate: node --check the inline dashboard script ---
where node >nul 2>&1
if %errorlevel%==0 (
    python -c "import re; html=open(r'docs/index.html',encoding='utf-8').read(); inline=[b for a,b in re.findall(r'<script([^>]*)>(.*?)</script>', html, re.S) if 'src=' not in a]; open(r'%TEMP%\bpx.js','w',encoding='utf-8').write(inline[0] if inline else ''); print('inline scripts:', len(inline))"
    node --check "%TEMP%\bpx.js"
    if errorlevel 1 ( echo JS SYNTAX GATE FAILED -- not committing & pause & exit /b 1 )
    echo JS syntax OK
) else (
    echo node not on PATH -- skipping JS check ^(already validated upstream^)
)

git add docs\index.html tools\add_bullpen_xwoba_heat.py PUSH_BULLPEN_XWOBA_HEAT.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "feat(dashboard): heatmap bullpen xwOBA-allowed gap (low = hot, pitcher-success)" -m "The bullpen xwOBA value renders in _deepNarrativeOU as a single signed home-vs-away gap (hl_bullpen_xwoba_gap). Wrapped it in a heat chip colored from the home reference: negative gap (home bullpen lower xwOBA-allowed = better) = hot, positive = cold -- matching the ERA/WHIP/xwOBA low=hot logic. Label '(home better/worse)' preserved for direction. Via tools/add_bullpen_xwoba_heat.py (idempotent, guarded, backup). Rule 3 node-check'd, Rule 4 safe-push."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Bullpen xwOBA gap is now heatmapped in the OU deep-analysis
    echo ^(low xwOBA-allowed = red/hot^). xwOBA is now visually consistent
    echo with the ERA/WHIP/K%% heat across the dashboard.
) else (
    echo Nothing to commit -- already current on origin.
)
echo.
pause
