@echo off
rem One-command verification on Windows: unit/safety tests, engine-vs-backtest replay, and (if Playwright is installed) the UI harness.
setlocal
cd /d "%~dp0"
set BPY=%LOCALAPPDATA%\ZackBot\buildenv\Scripts\python.exe
if not exist "%BPY%" set BPY=python
"%BPY%" -m pip install --disable-pip-version-check -q -r requirements-dev.txt || goto fail
echo [1/3] unit, safety, causality and parity tests (incl. slow, about 10 minutes)
"%BPY%" -m pytest -q -p no:cacheprovider tests || goto fail
echo [2/3] engine vs backtest replay (strict release gate, about 30-60 minutes)
set ZB_SIM_STEPS=24
set ZB_REPLAY_STRICT=1
if exist data\BTCUSDT_4h.csv (
  "%BPY%" test_engine_sim.py > dev_out_engine_sim.txt 2>&1 || goto fail
  findstr /b "TRADES STRICT GATE" dev_out_engine_sim.txt
  "%BPY%" test_engine_sim.py 3000 replay_scenario2.json > dev_out_engine_sim2.txt 2>&1 || goto fail
  findstr /b "TRADES STRICT GATE" dev_out_engine_sim2.txt
) else echo   skipped - the data folder is not present
echo [3/3] UI harness
"%BPY%" -c "import playwright" 2>nul
if errorlevel 1 (echo   skipped - run: pip install playwright ^&^& playwright install chromium) else ("%BPY%" test_app_ui.py || goto fail)
echo ALL CHECKS PASSED
exit /b 0
:fail
echo CHECKS FAILED
exit /b 1
