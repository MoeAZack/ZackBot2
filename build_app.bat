@echo off
title ZackBot installer
setlocal
set SRC=%~dp0
set ROOT=%LOCALAPPDATA%\ZackBot
set DST=%ROOT%\src
set VENV=%ROOT%\buildenv
set LOG=%ROOT%\build.log
if not exist "%ROOT%" mkdir "%ROOT%"
echo ==== ZackBot build %date% %time% ==== > "%LOG%"

echo [1/6] Stopping the old ZackBot (only ZackBot - nothing else)...
powershell -NoProfile -Command "Get-Process ZackBot -ErrorAction SilentlyContinue | Stop-Process -Force; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ZackBot\\src\\app\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >> "%LOG%" 2>&1

echo [2/6] Copying app files (no keys, logs or test data)...
robocopy "%SRC%." "%DST%" /E /XD __pycache__ .git tests data data1h data_long /XF build_app.bat config.env *.log session.json *.tmp /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1

set PY=python
where py >nul 2>nul && set PY=py
echo [3/6] Preparing a private build environment (pinned versions, your Python stays untouched)...
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" >> "%LOG%" 2>&1
set BPY=%VENV%\Scripts\python.exe
if exist "%BPY%" (
  "%BPY%" -m pip install --disable-pip-version-check -r "%DST%\requirements.txt" >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo   pinned install failed - using your existing Python packages instead >> "%LOG%"
    set BPY=%PY%
  )
) else (
  echo   could not create the build environment - using your existing Python >> "%LOG%"
  set BPY=%PY%
)

echo [4/6] Checking the build libraries...
"%BPY%" -c "import pandas, numpy, requests; print('libs ok', pandas.__version__, numpy.__version__)" >> "%LOG%" 2>&1

echo [5/6] Building ZackBot.exe (takes 1-3 minutes)...
"%BPY%" -m PyInstaller --noconfirm --onefile --windowed --name ZackBot --icon "%DST%\zackbot.ico" --add-data "%DST%\panel.html;." --add-data "%DST%\research;research" --collect-data tzdata --distpath "%ROOT%\app" --workpath "%TEMP%\zb_build" --specpath "%TEMP%\zb_build" "%DST%\app.py" >> "%LOG%" 2>&1

set TARGET=%ROOT%\app\ZackBot.exe
set ARGS=
set PSARGS=
if not exist "%TARGET%" (
  echo EXE build failed - using the Python version instead >> "%LOG%"
  if exist "%VENV%\Scripts\pythonw.exe" (set TARGET=%VENV%\Scripts\pythonw.exe) else (
    for /f "delims=" %%i in ('%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set TARGET=%%i
  )
  set ARGS="%DST%\app.py"
  set PSARGS=\"%DST%\app.py\"
)
echo [6/6] Creating desktop and Start-menu shortcuts...
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'ZackBot.lnk')); $s.TargetPath='%TARGET%'; $s.Arguments='%PSARGS%'; $s.WorkingDirectory='%DST%'; $s.IconLocation='%DST%\zackbot.ico'; $s.Description='ZackBot trading app'; $s.Save() }" >> "%LOG%" 2>&1
echo BUILD_DONE target=%TARGET% >> "%LOG%"
echo.
echo Done. Starting ZackBot...
start "" "%TARGET%" %ARGS%
timeout /t 5 >nul
