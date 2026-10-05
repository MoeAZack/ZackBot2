@echo off
title ZackBot UI baseline
rem Runs the browser/UI harness (test_app_ui.py) on Windows.
rem Safe while ZackBot is running: the harness starts its OWN copy on a free port with a temporary data folder and a fake
rem exchange. It never talks to Binance, never reads your keys and never touches %LOCALAPPDATA%\ZackBot data.
rem   run_ui_baseline.bat          full run (about 5-10 minutes)
rem   run_ui_baseline.bat quick    skips the long backtest/study jobs (about 2 minutes)
setlocal EnableExtensions
cd /d "%~dp0"
set ROOT=%LOCALAPPDATA%\ZackBot
set UIENV=%ROOT%\uienv
set OUTDIR=%~dp0dev_out
set LOG=%OUTDIR%\ui_baseline.log
set PYTHONIOENCODING=utf-8
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
echo ==== ZackBot UI baseline %date% %time% ==== > "%LOG%"

echo [1/4] Preparing the UI test environment (separate from the build environment)...
set PY=python
where py >nul 2>nul && set PY=py
if not exist "%UIENV%\Scripts\python.exe" %PY% -m venv "%UIENV%" >> "%LOG%" 2>&1
set UPY=%UIENV%\Scripts\python.exe
if not exist "%UPY%" (set WHY=could not create %UIENV% - is Python installed?& goto fail)
"%UPY%" -m pip install --disable-pip-version-check -q -r requirements-ui.txt >> "%LOG%" 2>&1
if errorlevel 1 (set WHY=installing the pinned libraries failed - internet or pip problem, see the log& goto fail)

echo [2/4] Installing the Chromium test browser (first time only, about 150 MB)...
"%UPY%" -m playwright install chromium >> "%LOG%" 2>&1
if errorlevel 1 (set WHY=downloading Chromium failed - internet, proxy or antivirus, see the log& goto fail)

echo [3/4] Checking the data files...
if not exist data\BTCUSDT_4h.csv (set WHY=the data folder is missing next to this file& goto fail)

echo [4/4] Running the UI harness (your running ZackBot is not touched)...
set ZB_OUT=%OUTDIR%
if /i "%~1"=="quick" (set ZB_UI_QUICK=1) else (set ZB_UI_QUICK=)
"%UPY%" test_app_ui.py >> "%LOG%" 2>&1
set RC=%errorlevel%
findstr /b /c:"FAIL " /c:"UI HARNESS" /c:"  failed:" "%LOG%"
if not "%RC%"=="0" (set WHY=one or more UI checks failed - see the list above& goto fail)
echo.
echo  UI baseline PASSED.
echo  Screenshots and summary.json: %OUTDIR%\ui_baseline
echo  Full log: %LOG%
echo.
pause
exit /b 0

:fail
echo UI_BASELINE_FAILED: %WHY% >> "%LOG%"
echo.
echo  *** UI BASELINE FAILED: %WHY%
echo  Full log: %LOG%
echo.
pause
exit /b 1
