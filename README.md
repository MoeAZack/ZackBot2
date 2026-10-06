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
If the new version is swapped in but does not answer with its build id, the installer restores `ZackBot.prev.exe`
(SHA-256 must equal the pre-install exe), restarts it and **proves the previous build is running again** (same HMAC ping);
the source mirror and shortcuts are only updated after a confirmed launch.

**Rollback drill:** double-click **rollback_drill.bat** (fixed mode: runs `installer.ps1 drill`). It installs the new build, makes it fail
its launch on purpose (`--simulate-failed-launch`, used only by the drill) and passes only if the rollback restores the
previous exe and confirms it running. The bot is stopped for about 1-2 minutes; exchange stops stay on Binance. Afterwards the
previous version is still installed - run `build_app.bat` normally to install the new one.
`build_app.bat preflight` is a non-destructive check (used by the tests): it stages the source into its own folder,
verifies the checksum helper and exits (log: `%LOCALAPPDATA%\ZackBot\build_preflight.log`); it never stops or replaces anything.
Settings shows the version and build id that is actually running.
`build_app.bat` is only a launcher: the installer logic lives in `installer.ps1` (one function per step, one fail-closed
error path, and an unexpected error after the old bot was stopped restores the previous version). Its whole flow is tested
with fakes in `tests/test_installer.py`; only the real install and the drill need the Windows PC.
Next to each readable log the installer writes a structured record (`build.json`, `build_preflight.json`, `build_check.json`):
Cairo timestamps, mode, build ids and hashes, one entry per step, rollback result and final verdict - no keys or settings.

Everything (keys, settings, logs, trades, backtests, candle cache) lives in `%LOCALAPPDATA%\ZackBot`.
Keys are stored encrypted for your Windows user (DPAPI).

Tabs: Dashboard · Trades · Risk · Strategies · Coins · Signals · Backtest Lab · Research · Logs · Settings · Help.
Closing the window keeps the bot trading in the background; reopen with the shortcut. Exchange stops stay on Binance either way.
All clock times in the panel are Cairo time (the bot's trading day).

## Version history
Run **setup_git.bat** once to turn this folder into a private Git repository with the full history (tags v2.2, v3.0, v3.0.1, v3.1, v3.2-rc1, v3.2-rc2) from `..\ZackBot2_git\zackbot_v3.2-rc2.1.bundle`. Then `git diff --stat v3.1 v3.2-rc2` shows exactly what changed. If Windows blocks Git from writing into Documents (Controlled folder access), the history is kept in `%LOCALAPPDATA%\ZackBot\history.git` and `..\ZackBot2_git\zbgit.bat` replaces `git` for this folder.

## Verify (developers / reviewers)
One command, three levels, one machine-readable summary per run (`dev_out/verify/latest_<level>.json`: commit, build id,
dataset manifest hash, dependency versions, pass/fail/skip counts, replay metrics, UI evidence path, exe hash, Cairo times):

| Level | What | Where |
|---|---|---|
| `verify fast` | every .py compiles, no private files / key-like strings, data files match `DATA_MANIFEST.json`, fast tests (`-m "not slow"`), installer preflight (Windows) | GitHub on every push; `verify.bat` (default) |
| `verify full` | all tests, both strict replays (24 steps, `replay.STRICT`), full UI harness, and on Windows `build_app.bat buildcheck` (PyInstaller build + exe self-test, nothing installed) | GitHub on pull requests into `master` and on demand; `verify.bat full` |
| `verify release` | full checks + rollback drill + read-only check of the running bot (engine/exchange ok, every lot protected, no untracked positions, no orphan orders) | the Windows PC: double-click `verify_release.bat` |

`python verify.py fast|full|release` runs the same thing anywhere (`release` on Windows only). `run_checks.bat/.sh` call `verify full`.
- `pytest` alone (repo root) runs the 200+ tests in `tests/`; `test_app_ui.py` and `test_engine_sim.py` are scripts used by `verify full`.
- `test_engine_sim.py [start_bar] [slots_json]` replays the LIVE engine against a simulated exchange and the backtester on the same
  candles. Scenario 2 (trailing stops, pyramiding, shorts): `python test_engine_sim.py 3000 replay_scenario2.json`.
- `test_app_ui.py`: headless browser harness on its own copy of the app (free port, temporary data folder, fake exchange, no
  internet). On Windows `run_ui_baseline.bat` sets up Playwright/Chromium in `%LOCALAPPDATA%\ZackBot\uienv` / `ms-playwright`.

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
