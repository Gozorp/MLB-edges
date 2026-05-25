@echo off
REM ============================================================================
REM PUSH_NEWBIE_UX.bat
REM Newbie UX scaffold:
REM   - First-visit intro card (dismissable, localStorage gate)
REM   - Help (?) button in header opens slide-over glossary panel
REM   - Simple / Advanced mode toggle hides F5/Fair/Edge/Pred/Tier/Claude
REM     in Simple mode (default for first-time visitors)
REM   - title= tooltips on every column header
REM Preserves Quant Terminal monospace + neon palette.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock del /F /Q .git\index.lock

echo === Staging + committing ===
git add docs\index.html _patch_newbie_ux.py PUSH_NEWBIE_UX.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(dashboard): newbie-friendly UX scaffold (intro + glossary + simple mode)" -m "Three-part newbie helper layer on top of Quant Terminal aesthetic, gated by localStorage so power users never see it after first dismissal." -m "1) Intro card auto-shown on first visit (#mlb-intro-card) with 4 numbered quick-tips: what the dashboard does, how to read a row, what grades mean, where to find help. Dismissable with X; sets localStorage.mlb_edge_intro_seen on dismiss." -m "2) Help button (?) added to header opens a right-side slide-over panel (#help-panel) with full column glossary (Matchup / Pick / F5 / Full / Fair / Edge / Pred / O/U / Tier / Grade / Claude / Result), grade legend (A through D), and how-to-use-it section. Backdrop + Escape close." -m "3) Simple / Advanced mode toggle near the slate header. Simple mode hides F5, Fair, Edge, Pred, Tier, Claude columns via body.simple-mode CSS class. First-time visitors default to Simple; returning users keep their last choice in localStorage.mlb_edge_slate_mode." -m "Plus title= tooltips on every th in the slate table so hovering on a column header explains it in plain English. Preserves monospace + neon palette per the locked Quant Terminal identity memory."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
