@echo off
setlocal EnableExtensions
rem ChatGPT <-> Claude Code Bridge launcher. Double-click to start.
rem Runs from its own folder and keeps all Python packages in .venv inside it.
set "BRIDGE_DIR=%~dp0"
cd /d "%BRIDGE_DIR%"
if errorlevel 1 goto :bad_folder

rem Check the extracted package before creating an environment or downloading anything.
for %%F in (requirements.txt bridge.py bridge_gui.py ui_i18n.py) do (
    if not exist "%BRIDGE_DIR%%%F" (
        set "MISSING_FILE=%%F"
        goto :missing_files
    )
)
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
if exist "%BRIDGE_DIR%.venv\Scripts\python.exe" goto :venv_ready
echo First run: creating a private Python environment in .venv ...
%PY% -m venv "%BRIDGE_DIR%.venv"
if errorlevel 1 goto :venv_failed
:venv_ready

fc /b "%BRIDGE_DIR%requirements.txt" "%BRIDGE_DIR%.venv\requirements.installed" >nul 2>&1
if not errorlevel 1 goto :deps_ready
echo Installing dependencies - this happens once and needs an internet connection ...
"%BRIDGE_DIR%.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%BRIDGE_DIR%requirements.txt"
if errorlevel 1 goto :deps_failed
copy /y "%BRIDGE_DIR%requirements.txt" "%BRIDGE_DIR%.venv\requirements.installed" >nul
:deps_ready

"%BRIDGE_DIR%.venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
if errorlevel 1 goto :no_tk

rem ---- Start the window ----------------------------------------------------
if /i "%~1"=="--console" (
    "%BRIDGE_DIR%.venv\Scripts\python.exe" "%BRIDGE_DIR%bridge_gui.py"
    pause
    exit /b 0
)
start "" "%BRIDGE_DIR%.venv\Scripts\pythonw.exe" "%BRIDGE_DIR%bridge_gui.py"
exit /b 0

:missing_files
echo.
echo [!] Incomplete extraction: "%MISSING_FILE%" is missing.
echo     Folder: "%BRIDGE_DIR%"
echo     Right-click the ZIP and choose Extract All.
echo     Open the extracted folder, then run Start Bridge.cmd from there.
echo     Do not run the CMD directly inside the ZIP or copy it alone.
echo.
echo     ZIP dosyasina sag tiklayip Tumunu Ayikla secenegini kullanin.
echo     CMD dosyasini ZIP icinden acmayin; tum dosyalar ayni klasorde olmali.
goto :fail

:bad_folder
echo.
echo [!] Could not open the application folder. Extract the full ZIP to a local folder.
goto :fail

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
echo [!] Installing dependencies failed. Read the pip error above for details, then run this file again.
if exist "%BRIDGE_DIR%.venv\requirements.installed" del "%BRIDGE_DIR%.venv\requirements.installed"
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
