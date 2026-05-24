@echo off
REM ============================================================================
REM PUSH_BULLPEN_DETAILED.bat
REM Pump up bullpen surfaces to Probable-Starters detail level.
REM   - Top-level Bullpen Outlook card: detailed panel with status badge,
REM     team-summary metrics, multi-sentence narrative, and inline fatigue
REM     table (was previously compact per Quant Terminal P3).
REM   - In-expander bullpen panel: K/9 stat added to line; per-reliever
REM     impact narrative below each row mirroring _pitcherImpact style.
REM
REM This intentionally overrides the "compact bullpen" rule from
REM feedback_quant_terminal_identity. Memory will be updated separately.
REM ============================================================================
cd /d D:\mlb_edge\mlb_edge

if exist .git\index.lock del /F /Q .git\index.lock

echo === Staging + committing ===
git add docs\index.html _patch_bullpen_detailed.py PUSH_BULLPEN_DETAILED.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(dashboard): bullpen surfaces to Probable-Starters detail level" -m "Surface A (top-level Bullpen Outlook card): _bullpenTeamNarrative replaced. Was: compact (status tag + pitch count + top arm). Now: detailed panel with status badge, team metrics line (pitches/72h + tracked + avg rest), multi-sentence narrative prose (state interpretation, fatigue alarms, rest interpretation, most-used arm callout), and the per-reliever fatigue table (rest/consec/P-72h/LI/flag) inline under the prose." -m "Surface B (in-expander bullpen panel): fmtBullpen rewritten. Was: 3-row li list with role label + ERA/WHIP/SV/HLD/IP + static role blurb. Now: same stats line plus K/9 (was missing), with per-reliever impact narrative below mirroring _pitcherImpact style for SPs (role context, K-rate read as 'punch-out arm' or 'contact-prone', ERA read as 'elite' or 'fringe', fatigue context from bullpen_meta when available)." -m "New helpers: _bullpenTeamProse, _relImpactNarrative. Fatigue context is looked up name-indexed from bullpen_meta sidecar so the in-expander narrative can surface FRESH/B2B/OVERWORKED context alongside roster ERA/WHIP/K9. Overrides 'compact bullpen cards' line in Quant Terminal identity per user request."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
