@echo off
REM Phase 1 of the Apple-style overhaul: full-viewport hero with
REM animated count-up stats, gradient background, smooth-scroll CTA,
REM and IntersectionObserver fade-in on cards as they enter viewport.
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

echo === Re-applying _patch_hero_section.py ===
python _patch_hero_section.py
if errorlevel 1 ( echo patch failed & pause & exit /b 1 )

echo === Staging + committing ===
git add docs\index.html _patch_hero_section.py PUSH_HERO.bat
if errorlevel 1 ( echo git add failed & pause & exit /b 1 )

git commit -m "feat(dashboard): Apple-style hero section + scroll reveals (Phase 1)" -m "First ship of the multi-phase Apple-style overhaul. Adds a full-viewport hero between header and main with: gradient background (animated radial pulse), eyebrow + large gradient-text headline + tagline, three count-up stats (games today / A-grade picks / live now), pill-shaped CTA that smooth-scrolls to the slate, floating scroll indicator at bottom. Hero markup is additive - the existing date picker, slate table, expanders, and newbie UX intro card all live below it intact." -m "Also adds an IntersectionObserver that fades-up .card and .preview-card elements as they enter the viewport, MutationObserver on #slate so newly-rendered rows get the same treatment. html { scroll-behavior: smooth } for anchor-link smoothness." -m "Phase 2 (slate row hover lift, animated grade badges, live-row pulse) and Phase 3 (deep-analysis section reveals, animated chart drawing) queued as separate ships."
if errorlevel 1 ( echo commit failed & pause & exit /b 1 )

echo === Push ===
git push origin main
if errorlevel 1 ( echo push failed & pause & exit /b 1 )

echo === DONE ===
git log -1 --oneline
pause
