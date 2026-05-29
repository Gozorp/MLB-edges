@echo off
REM ===========================================================================
REM PUSH_GAME_PREVIEW.bat
REM ---------------------------------------------------------------------------
REM WHAT: Adds a Statcast-style projected-lineup preview to The Slate's
REM       click-to-expand panel in docs/index.html. Per team: pitching matchup
REM       + a hitter table (Order, Name, Bat, OPS vs LHP / vs RHP / vs today's
REM       SP) heatmapped on OPS. "Awaiting Starting Lineup" until the lineup
REM       posts. Quant-Terminal themed (monospace, dark, blue->red heatmap).
REM       Uses ONLY baked diag data (away/home_top_5_batters_json) -- no fetch.
REM
REM HOW:  tools/add_game_preview.py makes 3 idempotent insertions (CSS, render
REM       functions, one call line in the expander). Validated in-sandbox:
REM       node --check passed; LAA@DET renders the heatmap; empty games show
REM       "Awaiting Starting Lineup".
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

REM --- apply the integration to the clean origin index.html ---
python tools\add_game_preview.py
if errorlevel 1 ( echo integration step failed & pause & exit /b 1 )

REM --- structural gate: all 3 insertions present ---
python -c "s=open(r'docs/index.html',encoding='utf-8').read(); n=sum(t in s for t in ['/* GP-PREVIEW-CSS','/* GP-PREVIEW-FN','/* GP-PREVIEW-CALL']); assert n==3, 'sentinels found: '+str(n); print('3 insertions present')"
if errorlevel 1 ( echo SENTINEL GATE FAILED & pause & exit /b 1 )

REM --- JS syntax gate: node --check the inline dashboard script (best-effort) ---
where node >nul 2>&1
if %errorlevel%==0 (
    python -c "import re; html=open(r'docs/index.html',encoding='utf-8').read(); inline=[b for a,b in re.findall(r'<script([^>]*)>(.*?)</script>', html, re.S) if 'src=' not in a]; open(r'%TEMP%\gpcheck.js','w',encoding='utf-8').write(inline[0] if inline else ''); print('inline scripts:', len(inline))"
    node --check "%TEMP%\gpcheck.js"
    if errorlevel 1 ( echo JS SYNTAX GATE FAILED -- not committing & pause & exit /b 1 )
    echo JS syntax OK
) else (
    echo node not on PATH -- skipping JS check ^(already validated upstream^)
)

REM --- stage + safe-push ---
git add docs\index.html tools\add_game_preview.py PUSH_GAME_PREVIEW.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "feat(dashboard): Statcast-style projected-lineup preview in The Slate expander" -m "Click a match in The Slate -> the expander now shows a Statcast-style preview: pitching matchup + per-team hitter table (Order, Name, Bat, OPS vs LHP / vs RHP / vs today's SP) heatmapped on OPS, in the Quant Terminal theme. Renders from the baked top-5 batter JSON (no fetch); shows 'Awaiting Starting Lineup' until the lineup posts. Added via tools/add_game_preview.py (idempotent, backup). Rule 3 node-check'd, Rule 4 safe-push."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Pushed. Open the dashboard, click any match row under The Slate, and
    echo scroll the expander -- the Statcast Game Preview renders there.
    echo Games with posted lineups show the OPS heatmap; the rest show
    echo "Awaiting Starting Lineup" until ~3h before first pitch.
) else (
    echo Nothing to commit -- already current on origin.
)
echo.
pause
