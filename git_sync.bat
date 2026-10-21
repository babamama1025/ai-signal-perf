@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo  git pull ^& commit ^& push  (%cd%)
echo ========================================
echo.

echo [1/4] git status
git status --short --branch
if errorlevel 1 goto :fail
echo.

echo [2/4] git pull --ff-only
git pull --ff-only
if errorlevel 1 (
    echo.
    echo [STOP] pull failed. Local and remote may have diverged, or local changes conflict.
    echo        Nothing was forced. Please resolve manually.
    goto :end
)
echo.

echo [3/4] git commit
git add -A
if errorlevel 1 goto :fail
git diff --cached --quiet
if not errorlevel 1 (
    echo No changes to commit.
    goto :push
)
git status --short
echo.
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd HH:mm'"') do set "NOW=%%i"
set "MSG=auto: sync %NOW%"
set /p "MSG=Commit message (Enter = "%MSG%"): "
git commit -m "%MSG%"
if errorlevel 1 (
    echo.
    echo [STOP] commit failed. Please check the message above.
    goto :end
)
echo.

:push
echo [4/4] git push
git push
if errorlevel 1 (
    echo.
    echo [STOP] push failed. Nothing was forced. Please check the message above.
    goto :end
)

echo.
echo [DONE] pull, commit and push completed.
goto :end

:fail
echo.
echo [STOP] git command failed. Is this a git repository?

:end
echo.
pause
endlocal
