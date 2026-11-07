@echo off
title ZackBot NEWCORE paper
rem NEWCORE PAPER runtime: install / start / stop / restart / rollback / status.  PAPER only, no keys, no network.
rem Usage: newcore_paper.bat install^|start^|stop^|restart^|rollback^|status
rem Files live in %LOCALAPPDATA%\ZackBotNewcore: paper_config.json and marks.json (you create them once; see
rem docs\newcore\RT02_PAPER_RUN.md), releases\, logs\, current.json and run.json.
rem Only the fixed words above are accepted (never re-expand free text into a CMD command line).
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=%LOCALAPPDATA%\ZackBotNewcore"
set "CMDWORD="
if /I "%~1"=="install" set "CMDWORD=install"
if /I "%~1"=="start" set "CMDWORD=start"
if /I "%~1"=="stop" set "CMDWORD=stop"
if /I "%~1"=="restart" set "CMDWORD=restart"
if /I "%~1"=="rollback" set "CMDWORD=rollback"
if /I "%~1"=="status" set "CMDWORD=status"
if not defined CMDWORD (
  echo Usage: newcore_paper.bat install^|start^|stop^|restart^|rollback^|status
  exit /b 64
)
if not exist "%ROOT%" mkdir "%ROOT%"
set "PYTHONIOENCODING=utf-8"
if "%CMDWORD%"=="install" python "%~dp0tools\newcore_release.py" install --root "%ROOT%" --source "%~dp0." --config "%ROOT%\paper_config.json"
if "%CMDWORD%"=="start" python "%~dp0tools\newcore_release.py" start --root "%ROOT%" --config "%ROOT%\paper_config.json" --marks "%ROOT%\marks.json"
if "%CMDWORD%"=="restart" python "%~dp0tools\newcore_release.py" restart --root "%ROOT%" --config "%ROOT%\paper_config.json" --marks "%ROOT%\marks.json"
if "%CMDWORD%"=="stop" python "%~dp0tools\newcore_release.py" stop --root "%ROOT%"
if "%CMDWORD%"=="rollback" python "%~dp0tools\newcore_release.py" rollback --root "%ROOT%"
if "%CMDWORD%"=="status" python "%~dp0tools\newcore_release.py" status --root "%ROOT%"
exit /b %errorlevel%
