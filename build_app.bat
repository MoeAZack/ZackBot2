@echo off
title ZackBot installer
setlocal EnableExtensions
rem Windows PowerShell 5.1 must use its OWN module paths. A PSModulePath inherited from another shell (e.g. PowerShell 7)
rem made Get-FileHash unavailable during the T03 drill and emptied the checksum. Cleared for this window only.
set "PSModulePath="
set SRC=%~dp0
set ROOT=%LOCALAPPDATA%\ZackBot
set DST=%ROOT%\src
set APPDIR=%ROOT%\app
set STAGE=%ROOT%\staging
set VENV=%ROOT%\buildenv
set LOG=%ROOT%\build.log
set WARN=
set HAVEOLD=0
set OLDBUILD=
set RBOK=0
set RBNOTE=
rem "build_app.bat drill" = rollback drill: install the new build, make it fail its launch on purpose
rem (--simulate-failed-launch), and require the installer to restore the previous version and prove it runs again.
set DRILL=0
if /i "%~1"=="drill" set DRILL=1
rem "build_app.bat preflight" = non-destructive check used by the tests: staging copy + checksum helper, then exit.
rem Own staging folder and log, no pause; never reaches step 2, so it cannot stop, swap or roll back anything.
set PREFLIGHT=0
if /i "%~1"=="preflight" set PREFLIGHT=1
if "%PREFLIGHT%"=="1" set STAGE=%ROOT%\staging_preflight
if "%PREFLIGHT%"=="1" set LOG=%ROOT%\build_preflight.log
rem "build_app.bat buildcheck" = verify full on Windows: steps 1-6 (staging, pins, tests, PyInstaller, exe self-test, hash) in
rem its own staging folder and log, then exit - never reaches step 7, so nothing is backed up, stopped or installed.
set BUILDCHECK=0
if /i "%~1"=="buildcheck" set BUILDCHECK=1
if "%BUILDCHECK%"=="1" set STAGE=%ROOT%\staging_buildcheck
if "%BUILDCHECK%"=="1" set LOG=%ROOT%\build_check.log
rem ZB_NOPAUSE=1 (set by verify.py) = never wait for a key press.
if not exist "%ROOT%" mkdir "%ROOT%"
echo ==== ZackBot build %date% %time% ==== > "%LOG%"
echo.
echo  The running ZackBot is NOT touched until the new build has passed every check.
echo  If the new version does not start correctly, the previous one is put back automatically.
echo.
if "%DRILL%"=="0" goto drill_banner_done
echo  *** ROLLBACK DRILL ***
echo  The new build is installed and then deliberately made to fail its launch. The installer must put the
echo  current version back and prove it is running again. The bot is stopped for about 1-2 minutes;
echo  your stops stay on Binance during that time.
echo.
echo   ROLLBACK DRILL requested >> "%LOG%"
if not exist "%APPDIR%\ZackBot.exe" goto fail_drill_noold
:drill_banner_done

echo [1/8] Copying the source to a clean staging folder...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
if exist "%STAGE%" goto fail_stage
robocopy "%SRC%." "%STAGE%\src" /MIR /XD __pycache__ .git .git_failed_* data data1h data_long dev_out /XF build_app.bat rollback_drill.bat setup_git.bat config.env *.log session.json *.tmp *.pkl build_info.py /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
if errorlevel 8 goto fail_copy
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set BUILD_ID=%%i
if "%BUILD_ID%"=="" goto fail_copy
> "%STAGE%\src\build_info.py" echo BUILD_ID = '%BUILD_ID%'
set CHK=powershell -NoProfile -ExecutionPolicy Bypass -File "%STAGE%\src\installer_check.ps1"
echo   build id %BUILD_ID% >> "%LOG%"
rem Preflight: the checksum helper must work in THIS environment before anything else (it is needed for the backup,
rem the swap and the rollback). Fails in seconds instead of after the tests and the build.
call :hash "%STAGE%\src\app.py" PREHASH
if "%PREHASH%"=="" goto fail_helper
echo   checksum helper ok >> "%LOG%"
if "%PREFLIGHT%"=="0" goto preflight_done
echo PREFLIGHT_OK build=%BUILD_ID% app.py sha256=%PREHASH% >> "%LOG%"
echo PREFLIGHT_OK app.py sha256=%PREHASH%
if exist "%STAGE%" rmdir /s /q "%STAGE%"
exit /b 0
:preflight_done

echo [2/8] Preparing the private build environment (pinned versions only)...
set PY=python
where py >nul 2>nul && set PY=py
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" >> "%LOG%" 2>&1
set BPY=%VENV%\Scripts\python.exe
if not exist "%BPY%" goto fail_venv
"%BPY%" -m pip install --disable-pip-version-check -r "%STAGE%\src\requirements-dev.txt" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_pip

echo [3/8] Checking the build libraries...
"%BPY%" -c "import pandas, numpy, requests, PyInstaller, pytest; print('libs ok', pandas.__version__, numpy.__version__, PyInstaller.__version__)" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_libs

echo [4/8] Running the safety tests...
pushd "%STAGE%\src"
"%BPY%" -m pytest -q -p no:cacheprovider -m "not slow" tests >> "%LOG%" 2>&1
set TESTS=%errorlevel%
popd
if not "%TESTS%"=="0" goto fail_tests

echo [5/8] Building ZackBot.exe (takes 1-3 minutes)...
"%BPY%" -m PyInstaller --noconfirm --onefile --windowed --name ZackBot --icon "%STAGE%\src\zackbot.ico" --add-data "%STAGE%\src\panel.html;." --add-data "%STAGE%\src\research;research" --collect-data tzdata --distpath "%STAGE%\dist" --workpath "%STAGE%\work" --specpath "%STAGE%\work" "%STAGE%\src\app.py" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_build
if not exist "%STAGE%\dist\ZackBot.exe" goto fail_build

echo [6/8] Self-test of the NEW exe (bundle, data files, time zones, version)...
start "" /wait "%STAGE%\dist\ZackBot.exe" --selftest "%STAGE%\selftest.json"
if not exist "%STAGE%\selftest.json" goto fail_selftest
powershell -NoProfile -Command "$r = Get-Content -Raw '%STAGE%\selftest.json' | ConvertFrom-Json; Write-Output ('selftest: ' + ($r | ConvertTo-Json -Compress)); if ($r.ok -and $r.build -eq '%BUILD_ID%') { exit 0 } else { exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_selftest
call :hash "%STAGE%\dist\ZackBot.exe" NEWHASH
if "%NEWHASH%"=="" goto fail_hash
echo   new exe sha256 %NEWHASH% >> "%LOG%"
if "%BUILDCHECK%"=="0" goto buildcheck_done
echo BUILDCHECK_OK build=%BUILD_ID% sha256=%NEWHASH% exe=%STAGE%\dist\ZackBot.exe >> "%LOG%"
echo BUILDCHECK_OK build %BUILD_ID% sha256 %NEWHASH%
exit /b 0
:buildcheck_done

echo [7/8] Backing up the current version (verified) before touching it...
if not exist "%APPDIR%" mkdir "%APPDIR%"
if exist "%APPDIR%\ZackBot.prev.exe" del /f /q "%APPDIR%\ZackBot.prev.exe" >> "%LOG%" 2>&1
if exist "%APPDIR%\ZackBot.prev.exe" goto fail_prevdel
if not exist "%APPDIR%\ZackBot.exe" goto no_old
copy /y "%APPDIR%\ZackBot.exe" "%APPDIR%\ZackBot.prev.exe" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_backup
call :hash "%APPDIR%\ZackBot.exe" OLDHASH
call :hash "%APPDIR%\ZackBot.prev.exe" PREVHASH
if "%OLDHASH%"=="" goto fail_backup
if /i not "%OLDHASH%"=="%PREVHASH%" goto fail_backup
set HAVEOLD=1
echo   backup ok sha256 %OLDHASH% >> "%LOG%"
rem Which build is the previous version? Needed to PROVE a rollback really brought it back (ping with its build id).
if exist "%STAGE%\old_selftest.json" del /f /q "%STAGE%\old_selftest.json"
start "" /wait "%APPDIR%\ZackBot.prev.exe" --selftest "%STAGE%\old_selftest.json"
for /f %%b in ('powershell -NoProfile -Command "try { (Get-Content -Raw '%STAGE%\old_selftest.json' | ConvertFrom-Json).build } catch { }"') do set OLDBUILD=%%b
echo   previous build %OLDBUILD% >> "%LOG%"
if "%DRILL%"=="1" if "%OLDBUILD%"=="" goto fail_drill_oldbuild
:no_old

echo [8/8] Installing: stopping the old ZackBot, swapping the exe, starting and checking the new one...
call :stopbot
if errorlevel 1 goto fail_stop
set TRY=0
:swap
copy /y "%STAGE%\dist\ZackBot.exe" "%APPDIR%\ZackBot.exe" >> "%LOG%" 2>&1
if not errorlevel 1 goto swapped
set /a TRY+=1
if %TRY% geq 5 goto rollback
timeout /t 2 /nobreak >nul
goto swap
:swapped
call :hash "%APPDIR%\ZackBot.exe" INSTHASH
if /i not "%INSTHASH%"=="%NEWHASH%" goto rollback
set LAUNCHARGS=
set PINGWAIT=60
if "%DRILL%"=="1" set LAUNCHARGS=--simulate-failed-launch
if "%DRILL%"=="1" set PINGWAIT=20
if "%DRILL%"=="1" echo   DRILL: launching the new exe with --simulate-failed-launch >> "%LOG%"
start "" "%APPDIR%\ZackBot.exe" %LAUNCHARGS%
%CHK% ping %BUILD_ID% %PINGWAIT% >> "%LOG%" 2>&1
if errorlevel 1 goto rollback
if "%DRILL%"=="1" goto fail_drill_noscenario
rem Only now, with the new version confirmed running, update the source mirror and the shortcuts (a rollback must not
rem leave %DST% describing a build that is not installed).
robocopy "%STAGE%\src" "%DST%" /MIR /XD tests /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
if errorlevel 8 set WARN=%WARN% [source copy in %DST% failed]
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'ZackBot.lnk')); $s.TargetPath='%APPDIR%\ZackBot.exe'; $s.Arguments=''; $s.WorkingDirectory='%APPDIR%'; $s.IconLocation='%APPDIR%\ZackBot.exe,0'; $s.Description='ZackBot trading app'; $s.Save() }" >> "%LOG%" 2>&1
if errorlevel 1 set WARN=%WARN% [desktop/start-menu shortcut not updated]
echo BUILD_DONE build=%BUILD_ID% sha256=%NEWHASH% target=%APPDIR%\ZackBot.exe >> "%LOG%"
if defined WARN echo BUILD_WARNINGS %WARN% >> "%LOG%"
echo.
echo Done - build %BUILD_ID% installed and confirmed running (Settings shows the same build id).
if defined WARN echo Warnings: %WARN%
if defined WARN if not defined ZB_NOPAUSE pause
timeout /t 5 >nul
exit /b 0

:rollback
echo ROLLBACK: the new version did not install or did not answer with build %BUILD_ID% >> "%LOG%"
call :stopbot
if "%HAVEOLD%"=="0" goto rollback_none
copy /y "%APPDIR%\ZackBot.prev.exe" "%APPDIR%\ZackBot.exe" >> "%LOG%" 2>&1
call :hash "%APPDIR%\ZackBot.exe" RBHASH
if /i not "%RBHASH%"=="%OLDHASH%" (set WHY=the new version failed AND restoring the previous exe failed - ZackBot.prev.exe is still in %APPDIR%& goto fail)
echo   restored exe sha256 %RBHASH% equals the pre-install hash >> "%LOG%"
start "" "%APPDIR%\ZackBot.exe"
if "%OLDBUILD%"=="" goto rb_unverified
%CHK% ping %OLDBUILD% 60 >> "%LOG%" 2>&1
if errorlevel 1 goto rb_noanswer
set RBOK=1
set RBNOTE=the previous version %OLDBUILD% was restored and confirmed running
goto rb_report
:rb_unverified
set RBNOTE=the previous version was restored and restarted, but its build id could not be read so it was not verified
goto rb_report
:rb_noanswer
set RBNOTE=the previous version was restored but did NOT confirm it is running - open ZackBot and check
:rb_report
echo ROLLBACK_RESULT verified=%RBOK% - %RBNOTE% >> "%LOG%"
if "%DRILL%"=="1" goto drill_report
set WHY=the new version did not start correctly - %RBNOTE%
goto fail
:drill_report
if not "%RBOK%"=="1" (set WHY=ROLLBACK DRILL FAILED - %RBNOTE%& goto fail)
echo DRILL_PASSED new=%BUILD_ID% failed its launch on purpose, restored=%OLDBUILD% sha256=%RBHASH% confirmed running >> "%LOG%"
echo.
echo  ROLLBACK DRILL PASSED: the new build %BUILD_ID% failed its launch on purpose, and the installer
echo  restored build %OLDBUILD% - same SHA-256 as before - and proved it is running again.
echo  Details: %LOG%
echo.
if not defined ZB_NOPAUSE pause
exit /b 0
:rollback_none
if exist "%APPDIR%\ZackBot.exe" del /f /q "%APPDIR%\ZackBot.exe" >> "%LOG%" 2>&1
set WHY=the new version did not start correctly (there was no previous version to restore)
goto fail

:fail_stage
set WHY=could not clear the staging folder %STAGE% (a file is open there?)
goto fail
:fail_copy
set WHY=copying the source failed
goto fail
:fail_venv
set WHY=could not create the build environment - is Python installed?
goto fail
:fail_pip
set WHY=installing the pinned libraries failed (internet / pip problem)
goto fail
:fail_libs
set WHY=the build libraries do not load
goto fail
:fail_tests
set WHY=the safety tests FAILED - this build is not safe to install
goto fail
:fail_build
set WHY=PyInstaller failed to build the exe
goto fail
:fail_selftest
set WHY=the new exe failed its self-test (missing files or wrong version)
goto fail
:fail_hash
set WHY=could not checksum the new exe - the reason is in the log
goto fail
:fail_helper
set WHY=the checksum helper does not work in this window - nothing was changed; the reason is in the log
goto fail
:fail_prevdel
set WHY=could not remove the old backup ZackBot.prev.exe (is it running?)
goto fail
:fail_backup
set WHY=backing up the current ZackBot.exe failed or the copy does not match - nothing was changed
goto fail
:fail_stop
set WHY=the running ZackBot did not stop - nothing was changed
goto fail
:fail_drill_noold
set WHY=rollback drill needs an installed ZackBot to roll back to - install normally first; nothing was changed
goto fail
:fail_drill_oldbuild
set WHY=rollback drill: could not read the installed version's build id, so a rollback could not be proven - nothing was changed
goto fail
:fail_drill_noscenario
set WHY=ROLLBACK DRILL INVALID: the new exe answered although it was told to fail - the new build %BUILD_ID% is running; check --simulate-failed-launch
goto fail
:fail
echo BUILD_FAILED: %WHY% >> "%LOG%"
echo.
echo  *** BUILD FAILED: %WHY%
echo  Details: %LOG%
echo.
if "%PREFLIGHT%"=="1" exit /b 1
if "%BUILDCHECK%"=="1" exit /b 1
if defined ZB_NOPAUSE exit /b 1
pause
exit /b 1

rem ---------------------------------------------------------------- helpers
:hash
set %2=
for /f %%h in ('%CHK% hash "%~1" 2^>^>"%LOG%"') do set %2=%%h
exit /b 0

:stopbot
powershell -NoProfile -Command "Get-Process ZackBot -ErrorAction SilentlyContinue | Stop-Process -Force; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ZackBot\\src\\app\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >> "%LOG%" 2>&1
set /a WT=0
:stopbot_wait
tasklist /fi "imagename eq ZackBot.exe" 2>nul | find /i "ZackBot.exe" >nul
if errorlevel 1 exit /b 0
set /a WT+=1
if %WT% geq 15 exit /b 1
timeout /t 1 /nobreak >nul
goto stopbot_wait
