@echo off
rem ZackBot public market-data collector (no API key, never trades). Arguments are passed through, e.g.
rem   tools\collect_market_data.bat --once
rem   tools\collect_market_data.bat --loop --every 4h
rem   tools\collect_market_data.bat --testnet          (testnet exchangeInfo snapshot for the BT02 exchange rules)
rem Default (no arguments) = --once. Output: %LOCALAPPDATA%\ZackBot\market_data (the app's folder; manifest.json, collector.log).
rem Python: ZackBot's own build environment if present, else the "py" launcher, else "python" on PATH.
setlocal EnableExtensions
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set "PYCMD="
if exist "%LOCALAPPDATA%\ZackBot\buildenv\Scripts\python.exe" set PYCMD="%LOCALAPPDATA%\ZackBot\buildenv\Scripts\python.exe"
if not defined PYCMD if exist "%LOCALAPPDATA%\ZackBot\uienv\Scripts\python.exe" set PYCMD="%LOCALAPPDATA%\ZackBot\uienv\Scripts\python.exe"
if not defined PYCMD (where py >nul 2>nul && set "PYCMD=py -3")
if not defined PYCMD set "PYCMD=python"
if "%~1"=="" (
  %PYCMD% "%~dp0collect_market_data.py" --once
) else (
  %PYCMD% "%~dp0collect_market_data.py" %*
)
exit /b %errorlevel%
