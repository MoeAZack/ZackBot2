@echo off
title ZackBot installer
setlocal EnableExtensions
set SRC=%~dp0
set ROOT=%LOCALAPPDATA%\ZackBot
set DST=%ROOT%\src
set APPDIR=%ROOT%\app
set STAGE=%ROOT%\staging
set VENV=%ROOT%\buildenv
set LOG=%ROOT%\build.log
set WARN=
set HAVEOLD=0
if not exist "%ROOT%" mkdir "%ROOT%"
echo ==== ZackBot build %date% %time% ==== > "%LOG%"
echo.
echo  The running ZackBot is NOT touched until the new build has passed every check.
echo  If the new version does not start correctly, the previous one is put back automatically.
echo.

echo [1/8] Copying the source to a clean staging folder...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
if exist "%STAGE%" goto fail_stage
robocopy "%SRC%." "%STAGE%\src" /MIR /XD __pycache__ .git data data1h data_long dev_out /XF build_app.bat setup_git.bat config.env *.log session.json *.tmp *.pkl build_info.py /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
if errorlevel 8 goto fail_copy
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set BUILD_ID=%%i
if "%BUILD_ID%"=="" goto fail_copy
> "%STAGE%\src\build_info.py" echo BUILD_ID = '%BUILD_ID%'
set CHK=powershell -NoProfile -ExecutionPolicy Bypass -File "%STAGE%\src\installer_check.ps1"
echo   build id %BUILD_ID% >> "%LOG%"

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
robocopy "%STAGE%\src" "%DST%" /MIR /XD tests /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
if errorlevel 8 set WARN=%WARN% [source copy in %DST% failed]
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'ZackBot.lnk')); $s.TargetPath='%APPDIR%\ZackBot.exe'; $s.Arguments=''; $s.WorkingDirectory='%APPDIR%'; $s.IconLocation='%APPDIR%\ZackBot.exe,0'; $s.Description='ZackBot trading app'; $s.Save() }" >> "%LOG%" 2>&1
if errorlevel 1 set WARN=%WARN% [desktop/start-menu shortcut not updated]
start "" "%APPDIR%\ZackBot.exe"
%CHK% ping %BUILD_ID% 60 >> "%LOG%" 2>&1
if errorlevel 1 goto rollback
echo BUILD_DONE build=%BUILD_ID% sha256=%NEWHASH% target=%APPDIR%\ZackBot.exe >> "%LOG%"
if defined WARN echo BUILD_WARNINGS %WARN% >> "%LOG%"
echo.
echo Done - build %BUILD_ID% installed and confirmed running (Settings shows the same build id).
if defined WARN echo Warnings: %WARN%
if defined WARN pause
timeout /t 5 >nul
exit /b 0

:rollback
echo ROLLBACK: the new version did not install or did not answer with build %BUILD_ID% >> "%LOG%"
call :stopbot
if "%HAVEOLD%"=="0" goto rollback_none
copy /y "%APPDIR%\ZackBot.prev.exe" "%APPDIR%\ZackBot.exe" >> "%LOG%" 2>&1
call :hash "%APPDIR%\ZackBot.exe" RBHASH
if /i not "%RBHASH%"=="%OLDHASH%" (set WHY=the new version failed AND restoring the previous exe failed - ZackBot.prev.exe is still in %APPDIR%& goto fail)
start "" "%APPDIR%\ZackBot.exe"
set WHY=the new version did not start correctly - the previous version was restored and restarted
goto fail
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
set WHY=could not checksum the new exe
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
:fail
echo BUILD_FAILED: %WHY% >> "%LOG%"
echo.
echo  *** BUILD FAILED: %WHY%
echo  Details: %LOG%
echo.
pause
exit /b 1

rem ---------------------------------------------------------------- helpers
:hash
set %2=
for /f %%h in ('%CHK% hash "%~1"') do set %2=%%h
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
