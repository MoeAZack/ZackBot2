@echo off
title ZackBot installer
rem ZackBot installer launcher (T03b). All installer logic lives in installer.ps1 (functions, real error handling, tested).
rem   build_app.bat              build + install: fail-closed, the running bot is untouched until every check passed,
rem                              automatic verified rollback if the new version does not start correctly
rem   build_app.bat drill        rollback drill (rollback_drill.bat)
rem   build_app.bat preflight    non-destructive check used by the tests (own staging folder and log)
rem   build_app.bat buildcheck   verify full on Windows: build + exe self-test, nothing installed
rem ZB_NOPAUSE=1 (set by verify.py) = never wait for a key press.
setlocal EnableExtensions
rem Windows PowerShell 5.1 must use its OWN module paths. A PSModulePath inherited from another shell (e.g. PowerShell 7)
rem made Get-FileHash unavailable during the T03 drill and emptied the checksum. Cleared for this window only.
set "PSModulePath="
set "ZBMODE=%~1"
if "%ZBMODE%"=="" set "ZBMODE=install"
rem The mode is passed QUOTED, so CMD metacharacters in it stay one argument and installer.ps1 refuses it (exit 2).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer.ps1" "%ZBMODE%"
set RC=%errorlevel%
rem 0 = done, 1 = failed (installer.ps1 already explained it and paused), 2 = unknown mode, other = installer.ps1 did not run.
if %RC% geq 2 echo  *** BUILD FAILED: installer.ps1 refused the mode or did not run (exit code %RC%). Details: %LOCALAPPDATA%\ZackBot
if %RC% geq 2 if not defined ZB_NOPAUSE pause
exit /b %RC%
