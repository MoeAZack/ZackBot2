@echo off
title ZackBot installer
set SRC=%~dp0
set ROOT=%LOCALAPPDATA%\ZackBot
set DST=%ROOT%\src
set LOG=%ROOT%\build.log
if not exist "%ROOT%" mkdir "%ROOT%"
echo ==== ZackBot build %date% %time% ==== > "%LOG%"

echo [1/5] Stopping the old ZackBot (if running)...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'ZackBot\\run_bot\.bat' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-Process ZackBot -ErrorAction SilentlyContinue | Stop-Process -Force" >> "%LOG%" 2>&1

echo [2/5] Copying app files...
robocopy "%SRC%." "%DST%" /E /XD __pycache__ /XF build_app.bat /NFL /NDL /NJH /NJS >> "%LOG%" 2>&1

set PY=python
where py >nul 2>nul && set PY=py
echo [3/5] Installing build tools (pyinstaller)...
%PY% -m pip install --upgrade pyinstaller requests pandas numpy >> "%LOG%" 2>&1

echo [4/5] Building ZackBot.exe (takes 1-3 minutes)...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name ZackBot --icon "%DST%\zackbot.ico" --add-data "%DST%\panel.html;." --add-data "%DST%\research;research" --distpath "%ROOT%\app" --workpath "%TEMP%\zb_build" --specpath "%TEMP%\zb_build" "%DST%\app.py" >> "%LOG%" 2>&1

set TARGET=%ROOT%\app\ZackBot.exe
set ARGS=
if not exist "%TARGET%" (
  echo EXE build failed - using the Python version instead >> "%LOG%"
  for /f "delims=" %%i in ('%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set TARGET=%%i
  set ARGS="%DST%\app.py"
)
echo [5/5] Creating desktop and Start-menu shortcuts...
powershell -NoProfile -Command "$w=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $d 'ZackBot.lnk')); $s.TargetPath='%TARGET%'; $s.Arguments='%ARGS%'; $s.WorkingDirectory='%DST%'; $s.IconLocation='%DST%\zackbot.ico'; $s.Description='ZackBot trading app'; $s.Save() }" >> "%LOG%" 2>&1
echo BUILD_DONE target=%TARGET% >> "%LOG%"
echo.
echo Done. Starting ZackBot...
start "" "%TARGET%" %ARGS%
timeout /t 5 >nul
