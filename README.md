# ZackBot — desktop app (v3.2)

## Install / update
Double-click **build_app.bat**. It is fail-closed:

1. copies the source to a clean staging folder and stamps a build id,
2. installs the pinned libraries into a private build environment (never your global Python),
3. runs the safety tests,
4. builds `ZackBot.exe` in staging,
5. runs the NEW exe's self-test (bundle, data files, time zones, build id),
6. only then stops the running bot, keeps the old exe as `ZackBot.prev.exe`, swaps in the new one and starts it.

If any step fails, the running bot is not touched and the window says why (details in `%LOCALAPPDATA%\ZackBot\build.log`).
Settings shows the version and build id that is actually running.

Everything (keys, settings, logs, trades, backtests, candle cache) lives in `%LOCALAPPDATA%\ZackBot`.
Keys are stored encrypted for your Windows user (DPAPI).

Tabs: Dashboard · Trades · Risk · Strategies · Coins · Signals · Backtest Lab · Research · Logs · Settings · Help.
Closing the window keeps the bot trading in the background; reopen with the shortcut. Exchange stops stay on Binance either way.
All clock times in the panel are Cairo time (the bot's trading day).

## Verify (developers / reviewers)
- `run_checks.bat` (Windows) or `./run_checks.sh`: unit + safety tests, engine-vs-backtest replay gate, UI harness if Playwright is installed.
- `pytest -q tests` alone needs only `requirements-dev.txt`.
- `test_engine_sim.py [start_bar] [slots_json]` replays the LIVE engine against a simulated exchange and the backtester on the same
  candles; it fails if the results differ by more than 15 points or the exchange position differs from what the engine tracks.
  Scenario 2 (trailing stops, pyramiding, shorts): `python test_engine_sim.py 3000 replay_scenario2.json`.
- `test_app_ui.py`: headless browser harness over every tab and API (needs `pip install playwright` + `playwright install chromium`).
  Screenshots go to `dev_out/` (or `$ZB_OUT`).

## Reproduce the research
Candle data: `data/` (40 coins 4h, 2 years), `data1h/` (core 8 1h, 6 months), `data_long/` (core 8: 4h 4.8 years, 1h 4.1 years).
Checksums, row counts and date ranges: `DATA_MANIFEST.json`.

| Table (Research tab) | Command |
|---|---|
| single strategies, 4h vs 1h, winner handling, v3.1 features, 452 combinations | `python research_refresh.py` (or one of `single,timeframes,runner,v31,combos`) |
| long history (presets, filters, options, single strategies) | `python research_long.py all` |
| Active alternatives, bull-only on all presets, Monte Carlo, walk-forward | `python research_long2.py` |
| grid / COMBO study | `python research_grid.py` |

Costs in every backtest: taker fee 0.05% and slippage 0.02% per fill, maker fee 0.02% (maker option), funding 0.005% per 4h bar (~0.03%/day average), 10x leverage cap,
8% daily loss halt, $500 start, compounded. Intrabar order: green candle open→low→high→close, red open→high→low→close.
