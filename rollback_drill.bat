@echo off
title ZackBot rollback drill
rem ZackBot installer ROLLBACK DRILL (T03). Double-click to run.
rem Builds and installs the current source like build_app.bat, then makes the new exe fail its launch on purpose
rem (--simulate-failed-launch). PASS = the installer restores the previous ZackBot.exe (same SHA-256) and proves the
rem previous build id is running again. The bot is stopped for about 1-2 minutes; your stops stay on Binance.
rem Afterwards the PREVIOUS version is the one installed. Run build_app.bat normally to install the new build.
rem Fixed mode, no arguments are read (T03b review: never re-expand free text into a CMD command line).
setlocal EnableExtensions DisableDelayedExpansion
set "PSModulePath="
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer.ps1" drill
set RC=%errorlevel%
if %RC% geq 2 echo  *** DRILL FAILED: installer.ps1 did not run (exit code %RC%).
if %RC% geq 2 if not defined ZB_NOPAUSE pause
exit /b %RC%
