@echo off
cd /d "%~dp0"
del /f /q ".git\index.lock" 2>nul
git pull --rebase --autostash
git add docs/index.html
git add .github/workflows/claude-brain.yml
git add tools/claude_brain_prompt.md
git add PUSH_BRAIN.bat
git commit -m "Claude Brain executive layer: daily 07:00 UTC workflow runs Claude Code Action (using Claude Max OAuth) to review the slate, read all postgame JSONs as memory, and write claude_picks/<date>.json with CONFIRM/DOWNGRADE/OVERRIDE per game. Dashboard adds Claude column with color-coded pill + reasoning tooltip. Setup: add CLAUDE_CODE_OAUTH_TOKEN secret in GitHub repo settings."
git push
pause
