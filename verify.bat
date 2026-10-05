@echo off
title ZackBot verify
rem   verify.bat fast      static checks, dataset manifest, fast tests, installer preflight          (about 3-5 min)
rem   verify.bat full      all tests, both strict replays, full UI harness, build + exe self-test    (about 40-60 min)
rem   verify.bat release   full checks + rollback drill + read-only check of the running bot        (about 60-80 min)
rem Summary: dev_out\verify\latest_<level>.json. Never changes your settings, keys or open trades; "release" stops the bot
rem for about 1 minute during the drill and restores it (your stops stay on Binance).
setlocal EnableExtensions
cd /d "%~dp0"
set "PSModulePath="
set PYTHONIOENCODING=utf-8
set LEVEL=%~1
if "%LEVEL%"=="" set LEVEL=fast
set VPY=%LOCALAPPDATA%\ZackBot\uienv\Scripts\python.exe
if not exist "%VPY%" set VPY=%LOCALAPPDATA%\ZackBot\buildenv\Scripts\python.exe
if not exist "%VPY%" (echo  No ZackBot Python environment found - run build_app.bat or run_ui_baseline.bat once first.& pause& exit /b 1)
set PLAYWRIGHT_BROWSERS_PATH=%LOCALAPPDATA%\ZackBot\ms-playwright
"%VPY%" verify.py %LEVEL%
set RC=%errorlevel%
echo.
if "%RC%"=="0" (echo  VERIFY %LEVEL% PASSED) else (echo  *** VERIFY %LEVEL% FAILED - see the list above and dev_out\verify\latest_%LEVEL%.json)
if not defined ZB_NOPAUSE pause
exit /b %RC%
