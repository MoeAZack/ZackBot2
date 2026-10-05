# ZackBot roadmap

## v3.2 (done - fixes from the independent GPT audit, 2026-10-05)
- DCA safety orders and pyramid adds pass one add-gate: daily halt, paused entries, confirmed stop, slot leverage cap on the share
  recorded at entry, portfolio risk rules (coin cap, open risk, funding); orphaned/replaced slots get no adds (stop + exits keep running)
- Fail-closed installer: staging build, pinned env only, tests + exe self-test + build-id check before the running bot is touched, rollback exe
- Deep merge of partial dca/pyramid/ttp/runner settings (engine, backtest, lab) + repair of lots saved by older versions
- Exit plan on every position (panel + Telegram), Cairo time everywhere in the panel, build id in Settings
- AI review prompt: real timeframe and side, no claims of news/listing knowledge, model/prompt/latency logged
- Backtester lookahead fixed: trailing stops used the ATR of the candle being walked; found by the engine-vs-backtest replay
- Reproducibility: conftest sandbox, requirements-dev, run_checks, replay gate, data manifest, research_refresh.py

## v3.2-rc2 (done - reviewer follow-up, 2026-10-05)
- BTC circuit breaker (enforce) pauses pyramid adds; DCA safety orders follow a per-basket policy fixed at entry: pause (default) / half_size / continue_plan
- replay.py: trade-level metrics (matched %, median/p95 |dR|, return and DD gap, reconciliation); strict targets are the release gate in run_checks (24 steps); both scenarios pass
- tests/test_causality.py: engine perturbation (unseen-price) test across 6 modes + per-trade parity on synthetic data + a leak detector proven to fail on the old same-candle-ATR bug
- installer: verified backup (hash), stale backup removed first, installed-exe hash check, source/shortcut checks, post-launch /api/ping with HMAC proof of the new build id, automatic rollback
- engine: no crash when no coin has 250 closed candles yet
- private Git history with version tags (setup_git.bat + bundle)

## Next (engineering)
- Shared core: move sizing, management levels, fees and state transitions into pure functions used by BOTH engine.py and
  backtest.py (the replay found one real drift; duplicated logic will drift again)
- More replay scenarios (gaps, minimum-order rounding, maker entries, risk-rule blocks) on real data
- Optional entry ranking when signals exceed free slots (today: fixed coin order, identical in live and backtest)
- CI pipeline running run_checks on every commit; Windows runner for the build script
- Point-in-time coin universe incl. delisted coins (LUNA, FTT ...) for the long tests; historical funding series
- Log every testnet maker/market fill to calibrate the backtest fill model

## v3.1 (done)
- Two-way Telegram control (/status /positions /pause /resume /flatten /profit ...)
- Order & exit types: multi take-profit ladder (up to 8), trailing take-profit, trailing entry, maker (limit/post-only) entries, pump/dump protection
- Range bots as toggles: futures grid (long/short/neutral), COMBO (DCA + grid), martingale DCA option, drawdown-recovery systems
- Backtest lab: parameter optimisation, walk-forward, Monte Carlo, lookahead-bias check, liquidation simulation
- Portfolio risk rules (off / warn / enforce): per-coin cap, total open-risk ceiling, correlated-positions cap, BTC circuit breaker, funding-rate filter
- Regime switching: market regime filters per slot (bull/bear/range) + equity rules (switch profile / scale risk after +X% or -Y%), suggest or auto

## Later (agreed with Moe, 2026-10-04)
1. Run 24/7 off the home PC: small cloud server or Windows service with watchdog/auto-restart
2. Full web control from mobile (secure remote panel), beyond Telegram commands
3. TradingView signal bot (Pine alerts -> webhook -> trades with ZackBot sizing/risk)
9. Copy-trading integration: Binance lead-trader portfolio, public stats page / monthly PDF for followers, tax/P&L export
10. Multiple accounts (sub-account per profile) and more exchanges (Bybit, OKX)
11. Shareable release: code signing, auto-update, crash reporting, licence keys, setup wizard
- Other: WebSocket user-data/mark streams instead of polling; 1-minute data for intrabar-exact backtests; CI pipeline; verify real-testnet order-lookup/algo-cancel paths and DPAPI after first v3 build
