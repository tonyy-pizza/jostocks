@echo off
rem  launch - open jostocks.
rem
rem  Double-click this file, or let install_autostart.ps1 put a shortcut in
rem  your Startup folder so it opens when you log on.
rem
rem  This reads what the scan wrote. It does not run the scan itself - use
rem  run_scan.bat in the stocks folder for that, or press refresh in the
rem  window, which runs it in the background.
rem
rem  ASCII only, on purpose: cmd.exe mangles non-ASCII under most code pages.

setlocal EnableExtensions
cd /d "%~dp0"
title jostocks

rem  A venv beside the project wins, so the app runs against whatever was
rem  installed for it rather than whatever is on PATH today.
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" jostocks.py %*
    goto :eof
)
if exist "venv\Scripts\pythonw.exe" (
    start "" "venv\Scripts\pythonw.exe" jostocks.py %*
    goto :eof
)

rem  pythonw first: no console window to leave sitting behind the app.
where pythonw.exe >nul 2>&1
if not errorlevel 1 (
    start "" pythonw.exe jostocks.py %*
    goto :eof
)

rem  The launcher knows where the real install is even when PATH does not.
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 (
    start "" py -3 -w jostocks.py %*
    goto :eof
)

echo.
echo   Could not find a working Python.
echo.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH" in the installer, then run this again.
echo.
pause
endlocal
