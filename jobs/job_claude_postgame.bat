@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
set "PY=python"
where python >nul 2>&1 || set "PY=py -3"
set "SLATE=%~1"
if "%SLATE%"=="" for /f %%d in ('%PY% -c "import datetime;from tools.slate_date import slate_today;print(slate_today()-datetime.timedelta(days=1))"') do set "SLATE=%%d"
echo ==== %DATE% %TIME% : claude-postgame slate %SLATE% ==== >> "logs\claude_postgame.log"
%PY% tools\fetch_results.py %SLATE% >> "logs\claude_postgame.log" 2>&1
claude -p "Read tools/claude_postgame_prompt.md and follow it, using ONLY Read/Write tools (no shell/Bash/curl). POST-MORTEM SLATE DATE = %SLATE%. Inputs: docs/data/picks_%SLATE%_diag.csv, docs/data/claude_picks/%SLATE%.json (if present; else NO_DECISION), every prior docs/data/postgame/*.json, and docs/data/_results_%SLATE%.json (the actual final game results -- use this, do NOT fetch). The docs/data/postgame/ folder already exists; write docs/data/postgame/%SLATE%.json per its schema. Finish by printing W/L counts and the patterns_observed list." --strict-mcp-config --dangerously-skip-permissions --tools "Read,Write,Glob,Grep" --max-turns 30 >> "logs\claude_postgame.log" 2>&1
%PY% tools\strip_bom.py docs\data\postgame\%SLATE%.json >> "logs\claude_postgame.log" 2>&1
echo ==== %DATE% %TIME% : claude-postgame done (exit %errorlevel%) ==== >> "logs\claude_postgame.log"
%PY% tools\publish_local.py postgame >> "logs\publish.log" 2>&1
