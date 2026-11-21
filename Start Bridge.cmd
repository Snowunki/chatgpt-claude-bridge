@echo off
setlocal EnableExtensions
rem ChatGPT <-> Claude Code Bridge launcher. Double-click to start.
rem Runs from its own folder and keeps all Python packages in .venv inside it.
cd /d "%~dp0"
title ChatGPT - Claude Code Bridge

rem ---- Python 3.10+ ---------------------------------------------------------
set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if not defined PY goto :no_python

rem ---- Google Chrome -------------------------------------------------------
set "HAVE_CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "HAVE_CHROME=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "HAVE_CHROME=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "HAVE_CHROME=1"
if not defined HAVE_CHROME goto :no_chrome

rem ---- Claude Code CLI -----------------------------------------------------
where claude >nul 2>&1
if errorlevel 1 goto :no_claude

rem ---- Private virtual environment -----------------------------------------
if exist ".venv\Scripts\python.exe" goto :venv_ready
echo First run: creating a private Python environment in .venv ...
%PY% -m venv .venv
if errorlevel 1 goto :venv_failed
:venv_ready

fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if not errorlevel 1 goto :deps_ready
echo Installing dependencies - this happens once and needs an internet connection ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :deps_failed
copy /y requirements.txt ".venv\requirements.installed" >nul
:deps_ready

".venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :no_tk

rem ---- Start the window ----------------------------------------------------
if /i "%~1"=="--console" (
    ".venv\Scripts\python.exe" bridge_gui.py
    pause
    exit /b 0
)
start "" ".venv\Scripts\pythonw.exe" bridge_gui.py
exit /b 0

:no_python
echo.
echo [!] Python 3.10 or newer was not found.
echo     Install it from https://www.python.org/downloads/windows/
echo     and tick "Add python.exe to PATH" in the installer. Then run this file again.
goto :fail

:no_chrome
echo.
echo [!] Google Chrome was not found.
echo     Install it from https://www.google.com/chrome/ and run this file again.
goto :fail

:no_claude
echo.
echo [!] Claude Code was not found - the "claude" command is not on PATH.
echo     Install it: https://docs.anthropic.com/en/docs/claude-code/setup
echo     Then open a terminal, run "claude" once and log in. Run this file again afterwards.
goto :fail

:venv_failed
echo.
echo [!] Could not create the .venv folder. Make sure this folder is writable,
echo     for example extract the ZIP to Documents instead of Program Files.
goto :fail

:deps_failed
echo.
echo [!] Installing dependencies failed. Check your internet connection and run this file again.
if exist ".venv\requirements.installed" del ".venv\requirements.installed"
goto :fail

:no_tk
echo.
echo [!] Tkinter is missing from your Python installation.
echo     Re-run the Python installer from python.org and enable "tcl/tk and IDLE".
goto :fail

:fail
echo.
pause
exit /b 1
