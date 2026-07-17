@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
set "SLATE=%~1"
if "%SLATE%"=="" for /f %%d in ('%PY% -c "from tools.slate_date import slate_today;print(slate_today())"') do set "SLATE=%%d"
echo ==== %DATE% %TIME% : claude-brain slate %SLATE% ==== >> "logs\claude_brain.log"
claude -p "Read tools/claude_brain_prompt.md and follow it for slate date %SLATE%, using ONLY Read/Write tools (no shell/Bash/curl). Inputs: docs/data/picks_%SLATE%_diag.csv plus every JSON under docs/data/postgame/. The docs/data/claude_picks/ folder already exists; write docs/data/claude_picks/%SLATE%.json in the schema that prompt specifies. Finish by printing how many CONFIRM / DOWNGRADE / OVERRIDE you produced." --strict-mcp-config --dangerously-skip-permissions --tools "Read,Write,Glob,Grep" --max-turns 30 >> "logs\claude_brain.log" 2>&1
%PY% tools\strip_bom.py docs\data\claude_picks\%SLATE%.json >> "logs\claude_brain.log" 2>&1
echo ==== %DATE% %TIME% : claude-brain done (exit %errorlevel%) ==== >> "logs\claude_brain.log"
%PY% tools\publish_local.py brain >> "logs\publish.log" 2>&1
