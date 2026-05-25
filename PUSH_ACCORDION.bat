@echo off
REM New "Ask the Slate" section + 3-item accordion wrapping the existing
REM #top-outcomes / #bullpen-outlook / #slate divs. All collapsed by
REM default; click any header to expand with smooth max-height transition
REM and chevron rotation. Inner div IDs preserved so rendering pipeline
REM still works unchanged.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

echo === Fetch + reset to origin/main ===
git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

echo === Re-applying _patch_accordion.py ===
python _patch_accordion.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Staging + committing ===
git add docs\index.html _patch_accordion.py PUSH_ACCORDION.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(dashboard): Ask the Slate accordion - Top Outcomes / Bullpen / Slate" -m "New section between the hero and the parlay block: 'Ask the Slate' heading with a 3-item accordion menu. Items: Top Probable Outcomes, Bullpen Outlook, The Slate. All collapsed by default. Click any header to expand the body with a smooth max-height + cubic-bezier transition; chevron rotates 180 degrees and shifts to accent color. aria-expanded set on each header." -m "The existing inner div IDs (#top-outcomes, #bullpen-outlook, #slate) are preserved inside the accordion bodies, so the rendering pipeline (renderTopProbableOutcomes, renderBullpenOutlook, renderSlate) keeps injecting into them with no changes." -m "Accordion sits inside #ask-the-slate-section with a gradient-text H2 matching the hero styling, eyebrow tag, and a card-style container with rounded corners + 1px borders. Inner cards have their default padding/background reset so they don't double-up inside the accordion body."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
