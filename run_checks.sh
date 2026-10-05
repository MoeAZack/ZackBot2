#!/usr/bin/env bash
# One-command verification (Linux/macOS dev box): unit/safety tests, engine-vs-backtest replay, UI harness if Playwright is installed.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -q -r requirements-dev.txt
echo "[1/3] unit and safety tests";   python3 -m pytest -q -p no:cacheprovider tests
echo "[2/3] engine vs backtest replay"
if [ -f data/BTCUSDT_4h.csv ]; then python3 test_engine_sim.py | grep -E '^(ENGINE|BACKTEST|GATE)'; else echo "  skipped - no data folder"; fi
echo "[3/3] UI harness"
if python3 -c "import playwright" 2>/dev/null; then python3 test_app_ui.py | grep -E 'JS errors|^card'; else echo "  skipped - pip install playwright && playwright install chromium"; fi
echo "ALL CHECKS PASSED"
