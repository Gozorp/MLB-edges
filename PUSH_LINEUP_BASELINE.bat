@echo off
REM Commit the lineup-matchup-gap probe baseline artifacts to origin.
REM Documents the negative-result Rule-2 probe that killed F6 before any
REM code was wired in. Same pattern as the blowout-magnitude baseline.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock
git rebase --abort 2>nul
git merge --abort 2>nul

REM Hard-sync to clean origin. Untracked baseline files survive reset.
git fetch origin main
git reset --hard origin/main

REM Re-run the probe to ensure CSV + JSON match the committed probe.py.
python data\baselines\lineup_matchup_gap_2026-04-27_to_2026-05-26\probe.py --start 2026-04-27 --end 2026-05-26 --out data\baselines\lineup_matchup_gap_2026-04-27_to_2026-05-26\
if errorlevel 1 ( echo probe failed & pause & exit /b 1 )

git add data\baselines\lineup_matchup_gap_2026-04-27_to_2026-05-26\probe.py data\baselines\lineup_matchup_gap_2026-04-27_to_2026-05-26\README.md data\baselines\lineup_matchup_gap_2026-04-27_to_2026-05-26\picks_with_gap.csv data\baselines\lineup_matchup_gap_2026-04-27_to_2026-05-26\summary.json PUSH_LINEUP_BASELINE.bat

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore(observability): persist lineup-matchup-gap probe baseline" -m "Rule-2 pre-flight probe that gated the proposed F6 'Lineup Dominance' conviction signal. AUC=0.4864 on n=184 pick-outcome pairs (BELOW the 0.50 random baseline, well below the 0.52 pre-locked kill threshold). Pearson r=-0.0189. Verdict: F6 killed before any code was wired in." -m "Root cause investigation confirmed that 'lineup_concentration' (mlb_edge/lineup_shape.py:concentration_index) measures top-heaviness (top-3 vs bottom-3 xwOBA ratio), not lineup-vs-SP matchup strength. The slight anti-correlation we measured matches the module's own docstring warning that top-heavy lineups are structurally vulnerable to competent pitching. A real lineup_matchup_gap (per-batter Log5 vs opposing SP) requires Phase 2-7 of the existing bottom-up sprint plan to land first." -m "Same documentation pattern as data/baselines/blowout_magnitude_2026-04-27_to_2026-05-25/ — README explains what/why/finding, probe.py is reusable for future windows, picks_with_gap.csv is the raw 184-row join, summary.json is the frozen result snapshot."
    if errorlevel 1 ( echo commit failed & pause & exit /b 1 )
    git pull --rebase --autostash origin main 2>nul
    git push origin main
    if errorlevel 1 ( echo push failed & pause & exit /b 1 )
    git log -1 --oneline
    echo.
    echo === DONE ===
    echo Lineup-matchup-gap probe baseline locked in project history.
) else (
    echo no changes to commit
)
pause
