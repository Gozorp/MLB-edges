@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  Dashboard UI: togglable cards
echo  -----------------------------------------------------------
echo  Every ^<h2^> inside a .card now acts as a clickable
echo  accordion header.  Click toggles every sibling element
echo  after the header.  Chevron ^(^&#9662;^) rotates 90 deg
echo  when collapsed.  Hover gives subtle opacity feedback.
echo
echo  Implementation: document-level event delegation, so it
echo  works for BOTH static cards ^(parlay, ask-claude, about^)
echo  AND dynamically-rendered cards ^(slate, top-outcomes,
echo  bullpen-outlook^).  Opt-in `card-toggle` class extends
echo  the behavior to ^<h3^> headers.  Opt-out via
echo  `card-no-toggle` class.
echo
echo  Built defensively per locked memory feedback_edit_tool_pivot:
echo  Python str.replace on clean origin tree, no Edit-tool calls
echo  on the 4925-line file.  Both CSS + JS hunks inserted at
echo  clean unique anchors ^(^</style^> + DOMContentLoaded listener^).
echo
echo  Files changed:
echo    1. docs/index.html  ^(+~80 lines: CSS rules + delegated
echo       click handler^)
echo    2. PUSH_TOGGLABLE_CARDS.bat ^(this file^)
echo
echo  Pre-Flight Prompt v1.0:
echo    [E] Rule 1  -- probed: origin healthy ^(4925 lines, 1 ^</html^>^);
echo                   ^</style^> + DOMContentLoaded both unique
echo                   string-search anchors
echo    [E] Rule 3  -- JS extracts as 2 blocks ^(~204k chars^);
echo                   node --check gate in this script
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- ONLY adds toggle behavior; zero existing
echo                   code paths modified; opt-out class available
echo                   for any header that should NOT toggle
echo    [E] Rule 6  -- event delegation degrades gracefully if
echo                   the click target isn't a direct .card child
echo                   ^(early return^); selection-detection prevents
echo                   double-click text-select from collapsing
echo    [E] Rule 11 -- ZERO production-pick paths touched; pure
echo                   visual UX change
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_togglable_cards
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"               "%TMPDIR%\docs\index.html"               >nul
copy /Y "PUSH_TOGGLABLE_CARDS.bat"      "%TMPDIR%\PUSH_TOGGLABLE_CARDS.bat"      >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring patched file...
copy /Y "%TMPDIR%\docs\index.html"               "docs\index.html"               >nul
copy /Y "%TMPDIR%\PUSH_TOGGLABLE_CARDS.bat"      "PUSH_TOGGLABLE_CARDS.bat"      >nul

echo File size + tail check...
python -c "s=open('docs/index.html', encoding='utf-8').read(); print(f'  size: {len(s)} chars, lines: {s.count(chr(10))+1}'); print(f'  tail: {s[-80:]!r}')"

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'  {len(blocks)} blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js

echo Staging + committing...
git add docs/index.html
git add PUSH_TOGGLABLE_CARDS.bat
git status --short
git commit -m "Dashboard UI: togglable cards via event delegation. Every <h2> inside a .card now acts as a clickable accordion header — click toggles display of all sibling elements after the header. Chevron rotates 90deg when collapsed; subtle hover opacity feedback. CSS hunk (~40 lines) inserted before </style>; JS hunk (~40 lines) inserted after the DOMContentLoaded ask-claude registration. Implementation uses document-level event delegation so it works for BOTH static cards (parlay, ask-claude, about) AND dynamically-rendered cards (slate, top-outcomes, bullpen-outlook). Opt-in 'card-toggle' class extends behavior to <h3>; opt-out 'card-no-toggle' class disables. Selection-detection prevents double-click text-select from accidentally collapsing. Built per locked memory feedback_edit_tool_pivot: Python str.replace on clean origin tree, no Edit-tool calls on the 4925-line file. Verified: 5006 lines, ends </script></body></html>, 2 script blocks (~204k chars), node --check passes. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (origin healthy, anchors unique), Rule 3 node --check, Rule 4 safe-push, Rule 5 zero existing code paths modified, Rule 6 graceful early-return on non-card clicks, Rule 11 zero production-pick paths touched (pure UX), Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS — togglable cards deployed.
echo
echo  Validate:
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^)
echo    2. Each section header ^(Top Probable Outcomes,
echo       Bullpen Outlook, Slate, Parlay, About^) shows
echo       a chevron ^(^&#9662;^) before the title
echo    3. Click any header -^> section collapses, chevron
echo       rotates 90 deg
echo    4. Click again -^> expands back
echo    5. Text selection inside a card does NOT collapse it
echo ============================================================
pause
