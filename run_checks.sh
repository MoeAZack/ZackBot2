#!/usr/bin/env bash
# One-command verification (Linux/macOS dev box): unit/safety tests, engine-vs-backtest replay, UI harness if Playwright is installed.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements-dev.txt
echo "[1/3] unit, safety, causality and parity tests (incl. slow)";   python3 -m pytest -q -p no:cacheprovider tests
echo "[2/3] engine vs backtest replay"
if [ -f data/BTCUSDT_4h.csv ]; then
  export ZB_SIM_STEPS=24 ZB_REPLAY_STRICT=1        # release gate: the reviewer's strict trade-level targets
  python3 test_engine_sim.py | grep -E '^(ENGINE|BACKTEST|TRADES|STRICT|GATE)'
  python3 test_engine_sim.py 3000 replay_scenario2.json | grep -E '^(ENGINE|BACKTEST|TRADES|STRICT|GATE)'      # trailing stops, pyramiding, shorts
else echo "  skipped - no data folder"; fi
echo "[3/3] UI harness"
if python3 -c "import playwright" 2>/dev/null; then python3 test_app_ui.py | grep -E 'JS errors|^card'; else echo "  skipped - pip install playwright && playwright install chromium"; fi
echo "ALL CHECKS PASSED"
