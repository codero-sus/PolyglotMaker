@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%SCRIPT_DIR%updater.py" %*
) else (
    python "%SCRIPT_DIR%updater.py" %*
)
exit /b %ERRORLEVEL%
