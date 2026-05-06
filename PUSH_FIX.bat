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

    echo --- git status before ---
    git status --short
    echo.

    echo --- staging ---
    git add docs .github mlb_edge models README.md .gitignore requirements.txt LICENSE setup_github.ps1 PUSH_FIX.bat
    echo done.

    echo --- removing untracked slate files that block rebase [workflow regenerates them daily] ---
    for /f "delims=" %%f in ('git ls-files --others --exclude-standard 2^>nul') do (
        echo %%f | findstr /R "^picks_.*\.csv$ ^parlay_.*\.txt$" >nul && (
            echo removing untracked: %%f
            del /q /f "%%f" 2>nul
        )
    )
    echo.

    echo --- staged files ---
    git diff --cached --name-only
    echo.

    echo --- committing ---
    git commit -m "Add BVP module + heuristic projected-lineup fallback (statsapi-only, no scraping)"
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
