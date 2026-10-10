@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE="

rem Read the digit-leading environment variable without batch parameter substitution.
for /f "tokens=1,* delims==" %%A in ('set 2PY2 2^>nul') do if /i "%%A"=="2PY2" set "PYTHON_EXE=%%B"

rem python.env: first non-empty, non-comment line; a raw path or PYTHON=/path is accepted.
if not defined PYTHON_EXE if exist "%SCRIPT_DIR%python.env" (
    for /f "usebackq eol=# tokens=* delims=" %%P in ("%SCRIPT_DIR%python.env") do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)

if defined PYTHON_EXE goto configured_python

where py >nul 2>nul
if errorlevel 1 goto fallback_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_version_error
py -3 "%SCRIPT_DIR%updater.py" %*
exit /b %ERRORLEVEL%

:fallback_python
where python3 >nul 2>nul
if not errorlevel 1 set "PYTHON_EXE=python3"
if not defined PYTHON_EXE (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_EXE=python"
)
if not defined PYTHON_EXE goto python_not_found

:configured_python
if /i "%PYTHON_EXE:~0,7%"=="PYTHON=" set "PYTHON_EXE=%PYTHON_EXE:~7%"
if /i "%PYTHON_EXE:~0,11%"=="PYTHON_EXE=" set "PYTHON_EXE=%PYTHON_EXE:~11%"
if /i "%PYTHON_EXE:~0,12%"=="PYTHON_PATH=" set "PYTHON_EXE=%PYTHON_EXE:~12%"
if /i "%PYTHON_EXE:~0,5%"=="2PY2=" set "PYTHON_EXE=%PYTHON_EXE:~5%"

if exist "%SCRIPT_DIR%%PYTHON_EXE%" set "PYTHON_EXE=%SCRIPT_DIR%%PYTHON_EXE%"
where "%PYTHON_EXE%" >nul 2>nul
if errorlevel 1 if not exist "%PYTHON_EXE%" goto python_not_found
"%PYTHON_EXE%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto python_version_error
"%PYTHON_EXE%" "%SCRIPT_DIR%updater.py" %*
exit /b %ERRORLEVEL%

:python_version_error
echo updater.bat: Python 3.10 or newer is required. 1>&2
exit /b 2

:python_not_found
echo updater.bat: Python executable not found. Set 2PY2 or configure python.env. 1>&2
exit /b 127
