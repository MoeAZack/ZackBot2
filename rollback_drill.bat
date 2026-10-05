@echo off
rem ZackBot installer ROLLBACK DRILL (T03). Double-click to run.
rem Builds and installs the current source like build_app.bat, then makes the new exe fail its launch on purpose
rem (--simulate-failed-launch). PASS = the installer restores the previous ZackBot.exe (same SHA-256) and proves the
rem previous build id is running again. The bot is stopped for about 1-2 minutes; your stops stay on Binance.
rem Afterwards the PREVIOUS version is the one installed. Run build_app.bat normally to install the new build.
call "%~dp0build_app.bat" drill
