# ZackBot roadmap

## v3.1 (in progress)
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
