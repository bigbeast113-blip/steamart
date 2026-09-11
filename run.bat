@echo off
REM Start SteamArt on Windows.
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 steamart.py %*
  goto :end
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python steamart.py %*
  goto :end
)

echo Python 3.7+ is required but was not found on PATH.
echo Install it from https://www.python.org/downloads/
pause

:end
