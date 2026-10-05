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
if not exist "%ROOT%" mkdir "%ROOT%"
echo ==== ZackBot build %date% %time% ==== > "%LOG%"
echo.
echo  The running ZackBot is NOT touched until the new build has passed every check.
echo  If anything fails, the old version simply keeps running.
echo.

echo [1/7] Copying the source to a clean staging folder...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
if exist "%STAGE%" goto fail_stage
robocopy "%SRC%." "%STAGE%\src" /MIR /XD __pycache__ .git data data1h data_long /XF build_app.bat config.env *.log session.json *.tmp *.pkl build_info.py /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
if errorlevel 8 goto fail_copy
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set BUILD_ID=%%i
if "%BUILD_ID%"=="" goto fail_copy
> "%STAGE%\src\build_info.py" echo BUILD_ID = '%BUILD_ID%'
echo   build id %BUILD_ID% >> "%LOG%"

echo [2/7] Preparing the private build environment (pinned versions only)...
set PY=python
where py >nul 2>nul && set PY=py
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" >> "%LOG%" 2>&1
set BPY=%VENV%\Scripts\python.exe
if not exist "%BPY%" goto fail_venv
"%BPY%" -m pip install --disable-pip-version-check -r "%STAGE%\src\requirements-dev.txt" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_pip

echo [3/7] Checking the build libraries...
"%BPY%" -c "import pandas, numpy, requests, PyInstaller, pytest; print('libs ok', pandas.__version__, numpy.__version__, PyInstaller.__version__)" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_libs

echo [4/7] Running the safety tests...
pushd "%STAGE%\src"
"%BPY%" -m pytest -q -p no:cacheprovider tests >> "%LOG%" 2>&1
set TESTS=%errorlevel%
popd
if not "%TESTS%"=="0" goto fail_tests

echo [5/7] Building ZackBot.exe (takes 1-3 minutes)...
"%BPY%" -m PyInstaller --noconfirm --onefile --windowed --name ZackBot --icon "%STAGE%\src\zackbot.ico" --add-data "%STAGE%\src\panel.html;." --add-data "%STAGE%\src\research;research" --collect-data tzdata --distpath "%STAGE%\dist" --workpath "%STAGE%\work" --specpath "%STAGE%\work" "%STAGE%\src\app.py" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_build
if not exist "%STAGE%\dist\ZackBot.exe" goto fail_build

echo [6/7] Self-test of the NEW exe (bundle, data files, time zones, version)...
start "" /wait "%STAGE%\dist\ZackBot.exe" --selftest "%STAGE%\selftest.json"
if not exist "%STAGE%\selftest.json" goto fail_selftest
powershell -NoProfile -Command "$r = Get-Content -Raw '%STAGE%\selftest.json' | ConvertFrom-Json; Write-Output ('selftest: ' + ($r | ConvertTo-Json -Compress)); if ($r.ok -and $r.build -eq '%BUILD_ID%') { exit 0 } else { exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 goto fail_selftest

echo [7/7] Installing: stopping the old ZackBot and swapping in the new exe (previous one kept for rollback)...
powershell -NoProfile -Command "Get-Process ZackBot -ErrorAction SilentlyContinue | Stop-Process -Force; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ZackBot\\src\\app\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >> "%LOG%" 2>&1
if not exist "%APPDIR%" mkdir "%APPDIR%"
if exist "%APPDIR%\ZackBot.exe" copy /y "%APPDIR%\ZackBot.exe" "%APPDIR%\ZackBot.prev.exe" >> "%LOG%" 2>&1
set TRY=0
:swap
timeout /t 2 /nobreak >nul
copy /y "%STAGE%\dist\ZackBot.exe" "%APPDIR%\ZackBot.exe" >> "%LOG%" 2>&1
if not errorlevel 1 goto swapped
set /a TRY+=1
if %TRY% lss 5 goto swap
goto fail_swap
:swapped
robocopy "%STAGE%\src" "%DST%" /MIR /XD tests /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'ZackBot.lnk')); $s.TargetPath='%APPDIR%\ZackBot.exe'; $s.Arguments=''; $s.WorkingDirectory='%APPDIR%'; $s.IconLocation='%APPDIR%\ZackBot.exe,0'; $s.Description='ZackBot trading app'; $s.Save() }" >> "%LOG%" 2>&1
echo BUILD_DONE build=%BUILD_ID% target=%APPDIR%\ZackBot.exe >> "%LOG%"
echo.
echo Done - build %BUILD_ID% installed. Starting ZackBot...
echo (Settings shows the build id; the previous version is kept as ZackBot.prev.exe)
start "" "%APPDIR%\ZackBot.exe"
timeout /t 5 >nul
exit /b 0

:fail_swap
echo SWAP FAILED - restoring the previous exe >> "%LOG%"
if exist "%APPDIR%\ZackBot.prev.exe" copy /y "%APPDIR%\ZackBot.prev.exe" "%APPDIR%\ZackBot.exe" >> "%LOG%" 2>&1
if exist "%APPDIR%\ZackBot.exe" start "" "%APPDIR%\ZackBot.exe"
set WHY=could not replace ZackBot.exe (file locked?) - the previous version was restarted
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
:fail
echo BUILD_FAILED: %WHY% >> "%LOG%"
echo.
echo  *** BUILD FAILED: %WHY%
echo  Your running ZackBot was NOT changed. Details: %LOG%
echo.
pause
exit /b 1
