@echo off
REM Fix: togglable-cards handler collapsing the slate when Simple/Advanced
REM mode toggle is clicked. Bail out for clicks on interactive controls
REM inside the header.
cd /d D:\mlb_edge\mlb_edge

set GIT_MERGE_AUTOEDIT=no
set EDITOR=true
set VISUAL=true
set GIT_EDITOR=true

if exist .git\index.lock del /F /Q .git\index.lock

git fetch origin main
git reset --hard origin/main
git clean -fd docs\data 2>nul

python _patch_toggle_card_collide.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

git add docs\index.html _patch_toggle_card_collide.py PUSH_TOGGLE_COLLIDE_FIX.bat
git commit -m "fix(dashboard): Simple/Advanced toggle no longer collapses the slate card" -m "Clicking the mode toggle inside the slate's h2 was bubbling to the togglable-cards delegation listener, which then collapsed the entire slate (display:none on all siblings after the header). User saw the slate disappear instead of the Advanced columns appearing. Bail out of togglable-cards for any click whose target is button/a/input/select/textarea/label inside the header. h2-as-collapse-trigger still works for actual header text clicks."

if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

git log -1 --oneline
pause
