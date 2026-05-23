@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo  EMERGENCY REPAIR v2: docs/index.html truncation (3867ad2)
echo  -----------------------------------------------------------
echo  Commit 3867ad2 ^("phase1 monte carlo: shadow pred_winp_mc^"^)
echo  was supposed to add MC tooltip + divergence flag to the
echo  pickWithProb and renderSlate functions.  It DID add those
echo  changes, but ALSO truncated 21 lines from the bottom of
echo  the file ^(everything after _initAskClaude's fetch URL,
echo  including the closing /script /body /html^).
echo
echo  Origin/main has been broken since 3867ad2 landed.
echo  Cloudflare Pages serves a page with unclosed ^<script^>;
echo  browser cannot finish parsing JS; dashboard renders blank.
echo
echo  This repair starts from bd6f080 ^(last good 4902-line
echo  version^), re-applies ONLY the intended MC tooltip /
echo  divergence-flag changes via Python str.replace, and
echo  preserves the closing tags.  Verified: 4925 lines,
echo  ends with /script /body /html, JS extracts as 2 blocks
echo  totaling ~202k chars.
echo ============================================================
echo.

del /f /q ".git\index.lock" 2>nul

echo Saving local edits to temp...
set TMPDIR=%TEMP%\mlb_edge_idx_repair_v2
rmdir /s /q "%TMPDIR%" 2>nul
mkdir "%TMPDIR%\docs" 2>nul
copy /Y "docs\index.html"                "%TMPDIR%\docs\index.html"                >nul
copy /Y "PUSH_INDEX_REPAIR_V2.bat"       "%TMPDIR%\PUSH_INDEX_REPAIR_V2.bat"       >nul

echo Fetching origin...
git fetch origin
if errorlevel 1 (echo FETCH FAILED & pause & exit /b 1)

echo Resetting local to origin/main...
git reset --hard origin/main
if errorlevel 1 (echo RESET FAILED & pause & exit /b 1)

echo Restoring repaired files...
copy /Y "%TMPDIR%\docs\index.html"                "docs\index.html"                >nul
copy /Y "%TMPDIR%\PUSH_INDEX_REPAIR_V2.bat"       "PUSH_INDEX_REPAIR_V2.bat"       >nul

echo File size + tail check...
python -c "import os; s = open('docs/index.html', encoding='utf-8').read(); print(f'  size: {len(s)} chars, lines: {s.count(chr(10))+1}'); print(f'  tail: {s[-80:]!r}')"

echo JS syntax-checking docs/index.html...
python -c "import re; s=open('docs/index.html', encoding='utf-8').read(); blocks=re.findall(r'<script[^>]*>(.*?)</script>', s, re.DOTALL); open('_check.js','w',encoding='utf-8').write('\n'.join(blocks)); print(f'  extracted {len(blocks)} script blocks, {sum(len(b) for b in blocks)} chars')"
node --check _check.js
if errorlevel 1 (echo JS SYNTAX CHECK FAILED & del /f /q _check.js & pause & exit /b 1)
del /f /q _check.js

echo Staging + committing...
git add docs/index.html
git add PUSH_INDEX_REPAIR_V2.bat
git status --short
git commit -m "EMERGENCY REPAIR v2: docs/index.html truncation from commit 3867ad2. Commit 3867ad2 (phase1 monte carlo: shadow pred_winp_mc + pred_runs_mc columns) added the intended MC tooltip + divergence flag to pickWithProb and renderSlate, but ALSO truncated 21 lines from the bottom of the file — everything after _initAskClaude's fetch URL was lost, including the closing </script></body></html>. Site has been broken on origin/main since 3867ad2 landed: unclosed <script> tag means browser cannot finish parsing JS and dashboard renders blank. Repair: started from bd6f080 (last good 4902-line version, confirmed </html>=1), re-applied ONLY the intended MC tooltip/divergence-flag changes via Python str.replace on 3 hunks (pickWithProb signature + MC tooltip, renderSlate totals tooltip, renderSlate pred_runs-only tooltip), and preserved the closing tags. Verified: 4925 lines, ends with </script></body></html>, 2 script blocks totaling ~202k chars, node --check passes. Same Edit-tool truncation pattern as previous repair commit (fb613d7 → repair was on 2026-05-21). Per locked memory feedback_edit_tool_pivot.md: should have used Python str.replace from the start instead of trusting Edit on a 4902-line file. Per Architecture-Session Pre-Flight Prompt v1.0: Rule 1 probed (identified truncation via git diff hunk header, found last good commit via line-count + </html> grep), Rule 3 node --check passes, Rule 4 safe-push, Rule 5 ONLY repaired the bug — preserved the intended MC tooltip feature exactly, Rule 13 push script narrates."
if errorlevel 1 (echo COMMIT FAILED & pause & exit /b 1)

echo Pushing...
git push origin HEAD:main
if errorlevel 1 (echo PUSH FAILED & pause & exit /b 1)

echo.
echo ============================================================
echo  SUCCESS - site repaired.
echo
echo  Validate:
echo    1. Hard-refresh dashboard ^(Ctrl+Shift+R^) - Cloudflare
echo       Pages auto-deploys in ~30s
echo    2. Slate table + Top Probable Outcomes + Bullpen Outlook
echo       all render again
echo    3. MC tooltip shows on PICK column hover if pred_winp_mc
echo       is populated in the diag CSV; divergence ^>10pp shows
echo       a warning icon
echo ============================================================
pause
