@echo off
setlocal enabledelayedexpansion
REM Non-interactive auto-push.  Writes everything to push.log.
REM Safe to run multiple times - re-pushing the same commit is a no-op.

set "LOG=%~dp0push.log"
> "%LOG%" 2>&1 (
    echo ========================================================
    echo  mlb_edge auto-push  -  %date% %time%
    echo ========================================================
    echo.

    echo --- pwd ---
    cd /d "%~dp0"
    cd
    echo.

    if not exist predict.py (
        echo ERROR: predict.py not found.  Wrong directory.
        exit /b 1
    )

    echo --- removing any stale .git/index.lock ---
    if exist ".git\index.lock" (
        del /f /q ".git\index.lock" 2>nul
        echo deleted stale lock
    ) else (
        echo no stale lock
    )
    echo.

    echo --- aborting any in-progress rebase/merge from a previous failed run ---
    git rebase --abort 2>nul
    git merge --abort 2>nul
    echo abort sequence complete [errors above are fine — means nothing to abort]
    echo.

    echo --- resolving any UU [unmerged] files by explicitly taking origin/main's version ---
    REM During a rebase, "--theirs" means the local commit being replayed; during a merge it means upstream.
    REM To avoid that inversion entirely, we fetch origin and check out origin/main's version directly.
    REM This guarantees we keep the auto-run's fresh data instead of clobbering it with stale local copies.
    git fetch origin main 2>&1
    python -c "import subprocess as s; out = s.check_output(['git','status','--porcelain'], text=True); files = [l[3:].strip() for l in out.splitlines() if l.startswith('UU')]; [s.run(['git','checkout','origin/main','--',f]) or s.run(['git','add',f]) for f in files]; print('resolved', len(files), 'unmerged files (using origin/main):', files)" 2>&1
    echo unmerged-resolution sequence complete
    echo.

    echo --- git status before ---
    git status --short
    echo.

    echo --- staging ---
    git add docs .github mlb_edge models README.md .gitignore requirements.txt LICENSE setup_github.ps1 PUSH_FIX.bat
    echo done.

    echo --- removing untracked picks/parlay files that block rebase [workflow regenerates daily] ---
    python -c "import os,subprocess; out=subprocess.check_output(['git','ls-files','--others','--exclude-standard'],text=True); removed=[f for f in out.splitlines() if (f.startswith('picks_') or f.startswith('parlay_')) and os.path.exists(f) and (os.remove(f) or True)]; print('removed', len(removed), 'untracked slate files:', removed)" 2>&1
    echo clean exit code: !errorlevel!
    echo.

    echo --- staged files ---
    git diff --cached --name-only
    echo.

    echo --- committing ---
    git commit -m "Two new A-tier caps: Stage 1/2 disagreement >= 0.18 caps at A-, negative edge_pp caps at A-"
    echo commit exit code: !errorlevel! [non-zero is fine if nothing new to commit]
    echo.

    echo --- pulling remote first [rebase + autostash for any unstaged noise] ---
    git pull --rebase --autostash 2>&1
    echo pull exit code: !errorlevel!
    echo.

    echo --- pushing ---
    git push 2>&1
    set PUSH_EXIT=!errorlevel!
    echo push exit code: !PUSH_EXIT!
    echo.

    echo --- final state ---
    git log -1 --oneline
    git remote -v

    if !PUSH_EXIT! EQU 0 (
        echo.
        echo SUCCESS
    ) else (
        echo.
        echo FAILED with code !PUSH_EXIT!
    )
)

REM Open the log so user can see what happened
notepad "%LOG%"
endlocal
exit /b 0
