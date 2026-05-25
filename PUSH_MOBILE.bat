@echo off
REM Mobile-friendly responsive layer: @media (max-width: 720px) +
REM (max-width: 480px) breakpoints. Shrinks hero, stacks grids,
REM enlarges tap targets to 44px, horizontally scrolls the slate
REM table with hint, disables hover transforms on touch.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

python _patch_mobile_friendly.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

git add docs\index.html _patch_mobile_friendly.py PUSH_MOBILE.bat
git commit -m "feat(dashboard): Apple-style mobile-friendly responsive layer" -m "Two breakpoints: @media (max-width: 720px) for tablet/large phone and (max-width: 480px) for small phone. Hero shrinks to 78vh with smaller fonts and tighter padding. Card padding 1.5rem -> 1.1rem on mobile, 0.9rem on small phone. All 2-column grids (intro card quick-tips, .preview-grid, Lineup-Edge per-side panels, Bullpen-Edge per-side panels, Bullpen Outlook team panels, .failure-grid) collapse to single column. Slate table becomes overflow-x scrollable on mobile with smaller cells (0.78rem font, 0.4rem padding) and an explicit 'swipe' hint underneath." -m "Tap targets: help button gets 44x44px (Apple HIG min), date picker arrows and buttons get min-height 40-44px, mode toggle buttons get larger padding, accordion headers get min-height 44px. Hover-driven transforms (hero CTA lift, accordion bg tint, row hover bg) are disabled inside @media (hover: none) so touch users don't see sticky hover state. Header meta tagline hides on <480px to save vertical space."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
pause
