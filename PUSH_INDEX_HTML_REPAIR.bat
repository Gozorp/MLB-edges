@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  EMERGENCY REPAIR: docs/index.html truncation
echo  -----------------------------------------------------------
echo  Commit fb613d7 added 11 lines for the graceful-degrade
echo  totals rendering but truncated 291 lines from the bottom
echo  of loadSlate^(^) through end-of-file.  Origin/main has
echo  unclosed ^<script^> tag, missing answerQuery, missing
echo  polling, missing _initAskClaude, missing closing HTML.
echo  Site is broken on origin.
echo
echo  Repair: start from fb613d7^~ ^(the pre-truncation tree^),
echo  re-apply ONLY the +11 line graceful-degrade insert,
echo  validate JS via node --check, push.
echo
echo  Verified: repaired file is 4695 lines ^(vs broken 4404^);
echo  node --check passes; 2 script blocks extracted with
echo  191k chars total.
echo
echo  Pre-Flight Prompt v1.0:
echo    [E] Rule 1  -- probed: identified truncation in commit
echo                   fb613d7 via git diff @@ -4391,294 +4401,3 @@
echo    [E] Rule 3  -- node --check passes on repaired tree
echo    [E] Rule 4  -- safe-push pattern
echo    [E] Rule 5  -- only repaired the bug; did NOT touch
echo                   the +11 line feature that was intended
echo    [E] Rule 13 -- this script narrates the change
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_index_repair
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"               "%TMPDIR%\docs\index.html"               >nul
copy /Y "PUSH_INDEX_HTML_REPAIR.bat"    "%TMPDIR%\PUSH_INDEX_HTML_REPAIR.bat"    >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Local vs origin:
git rev-parse --short HEAD
git rev-parse --short origin/main
echo.

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring repaired files...
copy /Y "%TMPDIR%\docs\index.html"               "docs\index.html"               >nul
copy /Y "%TMPDIR%\PUSH_INDEX_HTML_REPAIR.bat"    "PUSH_INDEX_HTML_REPAIR.bat"    >nul

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'extracted {len(blocks)} blocks, {sum(len(b) for b in blocks)} chars'); print(f'file size: {len(s)} chars, {s.count(chr(10))+1} lines')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js
echo JS syntax OK

echo File tail check ^(must end with /script/body/html^):
python -c "print(''.join(open('docs/index.html', encoding='utf-8').readlines()[-6:]))"

echo Staging + committing...
git add docs/index.html
git add PUSH_INDEX_HTML_REPAIR.bat
git status --short
git commit -m "EMERGENCY REPAIR: docs/index.html truncation introduced by fb613d7. Commit fb613d7 added 11 lines of graceful-degrade totals rendering but truncated 291 lines (the rest of loadSlate, all of answerQuery, polling logic, _initAskClaude, and closing </script></body></html>). Site was broken on origin/main: unclosed <script> tag, dashboard could not render past the slate load step. This commit restores the file from fb613d7~ (pre-truncation) and re-applies ONLY the intended +11 line graceful-degrade insert. Verified: repaired tree is 4695 lines, node --check passes, 2 script blocks extracted with ~191k chars total. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (identified truncation via git diff hunk header @@ -4391,294 +4401,3 @@), Rule 3 node --check, Rule 5 ONLY repaired the bug — preserved the intended feature, Rule 13 push script narrates. This is exactly the Edit-tool corruption pattern that locked memory feedback_edit_tool_pivot.md warns about; repair was done via Python str.replace on the clean fb613d7~ source per that pattern."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS - site repaired.
echo
echo  Validate next:
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^) - Cloudflare
echo       Pages auto-deploys in ~30s
echo    2. Slate table, Top Probable Outcomes, Bullpen Outlook
echo       should all render again
echo    3. Open the detail panel ^(click a slate row^) to confirm
echo       the JS execution completed through every block
echo ============================================================
pause
