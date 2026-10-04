# ZackBot v3.1: build report, audit and backtest results

*Prepared 2026-10-04 for review by another model. Author: Claude, working with Moe, the owner.*

This document describes the bot, what was built in v3.0 and v3.1, what the audits found and fixed, and the full set of backtests. It ends with open risks and the questions where outside input is wanted.

Two caveats apply to everything below:
- All numbers are backtests or a paper/testnet run. Nothing here is a forecast.
- Most results are **in-sample**: the presets were chosen after looking at the same data. Out-of-sample and robustness checks are reported separately and should carry more weight.

---

## 1. What ZackBot is

- **Purpose:** an automated crypto trading bot for **Binance USD-M perpetual futures**, run on a Binance **sub-account**.
- **Current stage:** testnet (paper) with a $500 base. Later it will be a copy-trading lead account.
- **Form:** a standalone **Windows desktop app**, a single `ZackBot.exe` built with PyInstaller.
  - It runs a local web panel (http.server on 127.0.0.1:8765) inside an Edge `--app` window.
  - It keeps running in the background when the window is closed.
- **Stack:**
  - Python 3.14 on the PC, 3.13 in the dev sandbox.
  - pandas 3.0.6, numpy 2.5.3, requests.
  - Data lives in `%LOCALAPPDATA%\ZackBot`.
- **Size:** about 8,900 lines of Python + HTML/JS.
- **Code layout:**

| File | Role |
|---|---|
| `binance_client.py` | REST client for signing, retries, idempotent orders and algo (conditional) orders |
| `engine.py` | Live engine for slots, lots, stops, reconcile, guards, capital, governor, risk rules, maker entries and presets |
| `strategies.py` | 10 signal strategies, indicators and the regime filter |
| `backtest.py` | Event backtester with the same sizing/management logic as the engine |
| `lab.py` | Optimiser, walk-forward, Monte Carlo, lookahead check, liquidation report |
| `grid.py` | Futures grid / COMBO bots (experimental) |
| `telegram_ctl.py` | Two-way Telegram control |
| `app.py` | HTTP API, background job queue, config, security, window/launcher |
| `panel.html` | Single-page UI with 11 tabs |
| `ai_filter.py` | Optional Claude-based trade filter (off) |

### 1.1 Trading model

- **Slots:** a profile is made of slots. Each slot has:
  - a strategy, a capital share and a risk % per trade;
  - max positions, coins (core 8 / top 40 / custom) and long/short;
  - a timeframe (4h, 1h or 15m) and management settings.
- **Cycles:** each timeframe runs its own cycle on candle close. A guard check runs every 60 s and marks are read every 8 s.
- **Sizing:** risk-based. Quantity = (risk % × sizing equity) / stop distance, with a leverage cap (default 10×).
- **Management options:**
  - fixed TP or partial TP;
  - trailing stop (ATR) and breakeven;
  - pyramiding;
  - DCA safety orders with a basket TP and stop;
  - "runner" mode (moving breakeven / lock steps);
  - time exit, Kelly fraction and an hours filter.
- **Strategies:**

| Key | Description |
|---|---|
| `ema_st` | EMA cross + Supertrend |
| `ema_mom` | EMA cross + momentum |
| `donchian_ens` | Donchian ensemble |
| `breakout_pyramid` | Breakout + pyramiding |
| `squeeze_tp` | Squeeze breakout with partial TP |
| `pullback_rsi` | RSI pullback |
| `dca_dip` | Dip buyer with safety orders |
| `bear_breakdown` | Shorts below BTC EMA200 |
| `rotation` | Relative-strength rotation |
| `hot_coin` | Hot-coin momentum |

- **Presets:**
  - Existing: Original, Calm, Balanced, Aggressive, Boost (4h), Active (1h), Boost+Active (mixed).
  - New in this pass: **Steady mix** and **Active DCA**.

### 1.2 Capital model

- **Bot capital** is separate from the Binance account balance (CAPITAL_CAP; the sub-account may hold more).
- **Fixed mode:** sizing equity = the start amount.
- **Compound mode:** start + closed P&L − withdrawals + deposits.
- **Capital cycles:** withdrawal, deposit, reset or "start fresh", all logged.
- **Guards** use bot capital including open P&L:
  - a daily loss halt (default 8%, on the Cairo trading day with DST handled);
  - an optional peak-drawdown flatten.

---

## 2. What was built

### v3.0: safety and correctness rebuild, plus a UI overhaul

**Order safety**
- Every order has a client order id. On a timeout or 5xx the order is looked up instead of being re-sent, so no duplicates.
- Only GET/DELETE are auto-retried, with backoff, Retry-After and re-signing.
- `AmbiguousOrder` carries the order tag so it can be resolved later.

**Stop safety**
- One exchange STOP_MARKET per lot, with an algo-order fallback.
- The new stop is placed first, then the old one is cancelled. `lot['stop']` only changes on success.
- A failed cancel is parked in an orphan list and retried.
- Positions are closed before their stops are cancelled.
- A lot is recorded before its stop is placed.

**Reconcile**
- A "pending" marker is set for unanswered close/add orders. It is resolved from the real exchange position.
- A shortfall is acted on only when seen twice and no order was sent in the last 30 s.
- Untracked positions are alerted after 2 readings.
- Dust below the minimum notional is ignored.

**Shared entry gate**
- One `entry_block()` covers auto, take-signal and manual entries.
- Manual trades are capped at 5% risk.

**Leverage**
- Uses the configured leverage.
- A margin-type error is non-fatal; leverage gets 1 retry. This was found on testnet, where the margin-type call returns "-1000 unknown error".

**Security**
- Per-launch token: HttpOnly SameSite=Strict cookie, or the `X-ZB-Token` header.
- Exact Host/Origin checks, JSON content-type enforced, size limits, CSP and security headers.
- Second-instance detection uses an HMAC challenge-response instead of trusting a ping.
- `config.env` secrets are **DPAPI-encrypted** for the Windows user. This is now verified on the real PC: keys are stored as `dpapi:` blobs and the bot authenticates.
- Config values are validated by regex and written atomically. Unreadable raw values are kept rather than wiped.
- A log scrub filter hides tokens and keys.
- Coin icons are PNG/JPEG/WEBP only, from Binance hosts only, with a 300 KB cap.
- Path and ID validation; inline handler values are escaped.

**Backtester**
- The book is aligned on the intersection of timestamps, and gaps are reported.
- Intrabar "path" logic: a green candle is treated as O→L→H→C, a red one as O→H→L→C.
- Gap fills for adds; a mark-to-market daily halt; net-R Kelly.
- Out-of-sample split of the last 30%, and a drawdown curve.

**UI**
- Glass/neon design with a safety bar and a money strip.
- Trades tab: open / closed / missed, ROI, P&L calendar and day drill-down.
- Risk & Exposure tab; coin logos; price chart modal; sortable tables.
- Onboarding, glossary, toggleable explanations, unsaved-changes bar, connection test.
- Backtest "honesty" badges (in-sample / out-of-sample / engine version).
- 4-year backtests; mixed 4h+1h backtests run as side-by-side sub-accounts.
- Compounding and withdrawal/reset cycles; "keep winners open" runner mode with a moving breakeven.

### v3.1: features (everything ships OFF or warn-only)

1. **Two-way Telegram control**
   - Commands: `/status /positions /profit /daily /risk /signals /pause /resume /flatten /close COIN`.
   - Only the configured chat id is answered.
   - `/flatten` and `/close` need a 6-character confirm code within 60 s, plus an optional PIN (pbkdf2-hashed).
   - Rate-limited. There is deliberately no remote "stop bot" command.
2. **Order and exit types**
   - Take-profit ladder (up to 8 levels) and trailing take-profit.
   - Trailing entry: wait for a turn-back of X ATR.
   - Maker (post-only) entries: re-priced N times, optional market fallback, maker fee setting.
   - Pump guard: skip entries after a candle above X ATR or a BTC 1h move above Y%.
3. **Range bots (experimental)**
   - Futures grid (long / short / neutral; ATR or % band; arithmetic or geometric spacing).
   - Hard stop beyond the band; only starts in a sideways regime; closes after trending.
   - COMBO (DCA entry + grid take-profits).
   - Risk preview: worst-case loss, liquidation distance, fee share, cycles to earn back.
   - Martingale DCA option, capped at n ≤ 8, scale ≤ 3, with a mandatory basket stop.
4. **Backtest Lab**
   - Parameter optimiser (random or grid search, hold-out check), walk-forward, Monte Carlo (trade shuffle and daily-block bootstrap), lookahead-bias check, liquidation report.
   - Progress display and cancel.
5. **Portfolio risk rules**, each Off / Warn / Enforce:
   - coin exposure cap;
   - total open-risk cap;
   - correlated-trades cap (30-day ρ);
   - BTC circuit breaker (optionally tightens stops);
   - funding-rate filter.
6. **Regime switching**
   - A per-slot market filter: any / bull / bear / range. Bull and bear come from BTC vs its daily EMA200; range from ADX and Bollinger width.
   - An equity "governor": rules like "risk × 0.5 after a 15% drawdown until a new high" or "switch profile after +100%". Modes: off / suggest / auto.

---

## 3. Audit: this pass (2026-10-04)

The PC came back online during this session, so the live testnet logs could be read.

| # | Finding | Severity | Status |
|---|---|---|---|
| A1 | **Algo-order cancel treated as failure.** Binance's `/fapi/v1/algoOrder` DELETE answers `{"code":"200","msg":"success"}` with a *string* code. The client only accepted numeric 0/200, so every algo-stop cancel was logged as failed and parked for retry. Seen live: `cancel stop BTCUSDT a:1000000228364523 failed (200: success)`. It was harmless in effect: the retry sees "order does not exist" and clears it. But it adds noise, and with many lots it could delay stop updates. | Medium | **Fixed.** String codes are normalised; regression test added. |
| A2 | **The Backtest tab and the Lab ignored the new per-slot v3.1 options** (`when` market filter, `trail_entry`, slot `pump_guard`). The Backtest tab also ignored the "run options" (maker entries, risk rules, governor), although the UI said they applied. In-app backtests of those features would have shown baseline numbers. | High (misleading results) | **Fixed** in `app.run_backtest_job` and `lab.resolve_sleeves`; test added. |
| A3 | **pandas 3 datetime-unit mismatch** (`datetime64[ms]` vs `[us]`) crashed `merge_asof`. This broke the regime filter, the Lab lookahead job and the BTC breaker with real 1h data. The live engine uses the same regime function, so **any slot with a market filter would have errored every cycle** on the pandas 3.0.6 build. | High | **Fixed.** Both sides are normalised to `[ns]` before every `merge_asof`. |
| A4 | **The Active (1h) preset is much riskier than its 6-month test showed.** Over 4.1 years of 1h data: max drawdown −63%. Monte Carlo: 23% chance of falling below half the start. The cause is the 1h breakout slot. Moe is currently running Boost+Active on testnet. | High (strategy risk) | Preset note now carries a warning. New presets **Active DCA (1h)** and **Steady mix** were added. Recommendation in §6. |
| A5 | **The "Tight ladder (1h)" winner setting was recommended from a 6-month test** ($961 → $1,235). Over 4 years it turned Active into $599 with a −67% drawdown. | Medium | UI text corrected. |
| A6 | **Telegram has never worked.** The saved chat id is the bot's own id ("bot can't send messages to the bot"). | Low (config) | UI warning added. Moe needs his own id from @userinfobot. |
| A7 | Startup logs 7 "invalid value ignored" warnings for legacy keys (SYMBOLS, RISK_PER_TRADE …). They come from an old config source; the current `config.env` doesn't contain them. | Low | Harmless; to clean up. |
| A8 | "Worst-case intrabar" mode gave identical results to "path" mode on all presets. Verified **not a bug**: it only affects stops that move inside a candle (breakeven / trailing / runner), and these presets use none. | – | – |
| A9 | The v3.1 UI build was cut off mid-way by a session limit. It was re-checked: all cards render, no JS errors, all endpoints exercised by the UI harness. | – | Verified |

**Verification after the fixes**
- 154 unit/safety tests pass.
- The Playwright UI harness passes: every tab, the new cards, settings round-trips, a Lab job, flatten, and no JS errors.
- Engine replay: the live engine against a fake exchange vs the backtester gives +62.3% vs +57.8%. Lots and stops match the exchange; only dust is left over.
- The v3.1 source has been copied to `Documents\ZackBot2`, with a backup at `Documents\ZackBot2_v3.0_backup`. Checksums verified.
- **The exe has not been rebuilt yet.** Moe has to run `build_app.bat`, which restarts the bot.

### Earlier audit history (all fixed in v3.0)

- **v2.2 issues:**
  - order retries that could duplicate orders;
  - cancel-before-close;
  - stale stops after failed updates;
  - unauthenticated local API;
  - config newline injection;
  - path traversal in backtest IDs;
  - Telegram token in logs.
- **A ChatGPT audit (ZB-01…ZB-20)** was reviewed and merged.
- **v3.0 re-review:**
  - lost order replies causing double TP/adds (→ pending markers);
  - untracked lost stops;
  - a stale positionRisk read dropping lots (→ the two-reading rule);
  - resyncs booked at ~0 P&L (→ booked at mark);
  - config wipe of invalid values;
  - installer fallback missing libraries;
  - a non-ASCII token crash;
  - ping spoofing (→ challenge-response);
  - a content-type substring check;
  - "0 = off" rejected;
  - a Cairo time-zone fallback edge;
  - testnet margin-type error blocking entries;
  - dust flagged as untracked.

---

## 4. Backtests

**Engine and costs.** Same sizing and management code as live:
- taker fee 0.04%, maker fee 0.02%, slippage modelled;
- funding modelled per bar;
- 10× leverage cap, 8% daily halt;
- path intrabar logic, maintenance-margin liquidation check;
- $500 start, compounded.

**Data sets**

| Set | Coins | Period | Notes |
|---|---|---|---|
| 2-year | 40 coins, 4h | 2024-09 → 2026-10 | The original preset-selection set (in-sample) |
| 6-month | core 8, 1h | 2026-04 → 2026-10 | |
| **NEW long history** | core 8 (BTC ETH SOL BNB XRP DOGE LINK AVAX), 4h | 2021-12 → 2026-10 (4.8 years, includes the 2022 bear market, LUNA and FTX) | From the PC's candle cache |
| **NEW long history** | core 8, 1h | 2022-08 → 2026-10 (4.1 years) | |

**Survivorship bias:** the 8 coins are today's survivors, so the long test favours them. "All coins" slots are limited to these 8 in the long tests. That is why the long-history Boost/Balanced numbers differ from the 40-coin ones.

### 4.1 Original 2-year results (40 coins, 4h; in-sample)

| Preset | $500 → | Max DD | Worst month | Trades/wk |
|---|---:|---:|---:|---:|
| Original | 3,223 | −39% | −7.8% | 2 |
| Calm | 1,545 | −13% | −3.3% | 7 |
| Balanced | 5,974 | −29% | −7.3% | 10 |
| Aggressive | 11,886 | −39% | −10.8% | 10 |
| Boost | 23,792 | −54% | −27.8% | 9 |

On 1h, last 6 months only:

| Preset | $500 → | Max DD | Worst month | Trades/wk |
|---|---:|---:|---:|---:|
| Active | 961 | −24% | −3.8% | 20 |
| Boost+Active | 1,196 | −32% | −8.0% | 29 |

2-year, 40 coins, with the bull-only filter on trend slots:

| Preset | Base | Bull-only |
|---|---|---|
| Calm | 1,545 / −13.3% | 1,261 / −13.2% |
| Balanced | 5,974 / −29.2% | 3,722 / −24.3% |
| Aggressive | 11,886 / −38.8% | 6,839 / −32.7% |
| Boost | 23,792 / −54.4% | 16,997 / −47.4% |

In this window the filter mostly cost return: it sat out the 2026 recovery.

### 4.2 v3.1 feature tests (2-year, 40 coins)

| Variant | $500 → | Max DD | Note |
|---|---:|---:|---|
| Balanced baseline | 5,974 | −29.2% | |
| + TP ladder 1/2/3R × 25% | 1,866 | −23.8% | Cuts the big trends |
| + trailing TP 2R / 3% | 1,354 | | |
| + maker entries | 6,181 | | |
| + maker, no fallback | 6,300 | | |
| + pump guard 3 ATR | 4,794 | −32.8% | |
| + BTC breaker 3%/h (4h proxy) | 5,332 | | |
| bull-only trend slots | 3,722 | −24.3% | |
| bull-only + bear_breakdown | 3,381 | | |
| Boost baseline | 23,792 | −54% | worst month −28% |
| Boost + governor (DD ≥ 15% → risk × 0.5) | 15,547 | −35.8% | worst month −14.7% |
| Boost + governor (growth ≥ 100% → × 0.33) | 10,718 | −28.4% | worst month −11% |
| Boost + martingale (risk × 1.5 after 15% DD) | 14,807 | −68.9% | Worse on every measure |

- No liquidations in any test.
- `martingale_risk` for the default `dca_dip`: worst-case basket loss 1.02R; peak notional 15.9× per $ of risk.

### 4.3 Grid / COMBO research (2-year)

- **0 of 32** grid configurations were profitable. The best (neutral, ATR×6, 10 levels) went $500 → $438 with a −16% drawdown.
- COMBO ended roughly flat ($475–516); its 1h gain looks overfit.
- Correlation with Balanced is low but noisy, and no diversification benefit showed up.
- **Decision:** ship grids disabled, labelled experimental.

### 4.4 Lab results (2-year)

- **Lookahead check:** no leaks across all 10 strategies, all indicators and the regime filter.
- **Monte Carlo** (daily-block bootstrap; the trade-shuffle version understates risk):
  - Balanced: median max drawdown −33%, 5% worst −51%, P(drawdown ≥ 50%) 5.5%, P(fall below ½ start) 0.55%.
  - Boost: median −59%, 5% worst −80%, P(drawdown ≥ 50%) 80%, P(below ½ start) 11.7%.
- **Walk-forward on Balanced:**
  - re-optimised out-of-sample: +239% / −39%;
  - untouched defaults: +296% / −28%;
  - efficiency 0.37; 56% of windows profitable.
- **Optimiser on Calm:** the top trial overfits. Hold-out Calmar was 4.70 vs 7.46 for the baseline.

### 4.5 NEW: long-history backtests

These tables were generated directly from the result files; nothing was retyped.

- 4h tests start 2022-01-25, after a 220-bar warm-up. 1h tests start 2022-09-03.
- 2026 is year-to-date (to 2026-10-04).
- "Trades/wk" is lower than in the 40-coin tests because only 8 coins are traded.

**A. Presets, long history**

| Test | TF | $500 → | CAGR | Max DD | Worst mo | Trades/wk | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| original | 4h | 5,188 | 65% | -52.7% | -19.0% | 1.9 | -34% | +114% | +150% | +47% | +100% |
| calm | 4h | 1,169 | 20% | -13.3% | -4.1% | 2.4 | -7% | +36% | +32% | +12% | +25% |
| balanced | 4h | 3,593 | 52% | -33.6% | -8.5% | 3.5 | -20% | +91% | +104% | +33% | +72% |
| aggressive | 4h | 6,921 | 75% | -45.2% | -11.9% | 4.0 | -27% | +129% | +167% | +52% | +106% |
| boost | 4h | 9,274 | 86% | -41.0% | -11.8% | 3.4 | -27% | +226% | +95% | +60% | +148% |
| active | 1h | 3,172 | 57% | -63.2% | -25.3% | 19.8 | -29% | +26% | +144% | +47% | +99% |
| active + tight ladder | 1h | 599 | 4% | -66.7% | -20.3% | 18.7 | -31% | +18% | +6% | -34% | +112% |
| boost_active (mixed, from 2022-09) | 4h+1h | 7,726 | – | -40.2% | – | – | – | – | – | – | – |

**B. Bull-only market filter on trend slots (4h)**

| Test | TF | $500 → | CAGR | Max DD | Worst mo | Trades/wk | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| original | 4h | 5,336 | 66% | -29.5% | -19.0% | 1.3 | +0% | +113% | +150% | +60% | +25% |
| original + maker | 4h | 5,362 | 66% | -28.5% | -18.8% | 1.3 | +0% | +104% | +157% | +63% | +25% |
| calm | 4h | 1,097 | 18% | -12.2% | -4.1% | 1.9 | -0% | +36% | +32% | +14% | +8% |
| calm + maker | 4h | 1,077 | 18% | -10.4% | -4.1% | 1.9 | -0% | +32% | +33% | +15% | +8% |
| balanced | 4h | 3,445 | 51% | -22.1% | -8.1% | 3.2 | +1% | +94% | +110% | +40% | +20% |
| balanced + maker | 4h | 3,347 | 50% | -22.2% | -8.0% | 3.1 | +1% | +86% | +112% | +40% | +20% |
| aggressive | 4h | 7,082 | 76% | -30.0% | -11.6% | 3.5 | -0% | +144% | +180% | +60% | +30% |
| aggressive + maker | 4h | 7,050 | 76% | -29.8% | -11.4% | 3.5 | +2% | +132% | +185% | +61% | +30% |
| boost | 4h | 8,567 | 83% | -31.0% | -10.2% | 3.2 | +1% | +222% | +103% | +62% | +61% |
| boost + maker | 4h | 9,375 | 87% | -30.8% | -10.1% | 3.2 | +5% | +225% | +111% | +61% | +60% |
| boost + governor halve 15% DD | 4h | 3,020 | 47% | -26.4% | -10.2% | 3.0 | +1% | +99% | +34% | +40% | +61% |

**C. Active (1h) alternatives, 2022-09 → 2026-10**

| Test | TF | $500 → | CAGR | Max DD | Worst mo | Trades/wk | 2022* | 2023 | 2024 | 2025 | 2026 YTD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| DCA1H only 2% | 1h | 4,637 | 73% | -25.9% | -13.6% | 11.7 | +22% | +133% | +94% | +50% | +12% |
| DCA1H only 3% | 1h | 13,783 | 126% | -36.9% | -20.0% | 11.7 | +36% | +256% | +168% | +82% | +17% |
| DCA1H only 2% maker | 1h | 4,752 | 74% | -24.2% | -13.5% | 11.7 | +21% | +136% | +90% | +53% | +15% |
| DCA1H only 2% maker no-fallback | 1h | 4,708 | 73% | -24.2% | -13.5% | 11.7 | +21% | +135% | +89% | +53% | +14% |
| Active BRK bull-only | 1h | 2,746 | 52% | -57.7% | -25.0% | 17.9 | +5% | +13% | +91% | +116% | +12% |
| Active BRK bull-only + maker | 1h | 4,189 | 68% | -53.6% | -24.9% | 18.1 | +5% | +31% | +112% | +146% | +16% |
| Active maker no-fallback | 1h | 4,121 | 68% | -60.4% | -25.1% | 19.9 | -30% | +40% | +136% | +67% | +113% |
| Active maker (with fallback) | 1h | 5,753 | 82% | -58.6% | – | 20.1 | -27% | +53% | +169% | +72% | +124% |
| Active 1% risk | 1h | 1,687 | 35% | -39.3% | -13.7% | 17.6 | -14% | +12% | +61% | +31% | +67% |
| Active governor halve 15% DD | 1h | 2,196 | 44% | -44.1% | -13.9% | 18.0 | -17% | +12% | +82% | +46% | +78% |

\* 1h data starts 2022-09-03.

**Suspicious result.** Maker entries add very little on 4h but a lot on Active 1h ($3,172 → $5,753). The maker model fills a post-only order if price trades through the limit within the wait window; otherwise it falls back or skips. On 1h breakouts, skipping unfilled entries may be filtering out the worst breakouts, which would be selection, not fees. This needs a fill-realism check (see Q4).

**D. Single strategies, 2% risk, max 4 positions, core 8**

| Strategy | TF | $500 → | CAGR | Max DD | Worst mo | Trades/wk | 2022 | 2023 | 2024 | 2025 | 2026 YTD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| breakout_pyramid | 4h | 5,031 | 64% | -62.5% | – | 2.3 | -27% | +233% | +72% | +138% | +1% |
| ema_st | 4h | 3,899 | 55% | -46.3% | – | 1.1 | -30% | +77% | +178% | +36% | +67% |
| ema_mom | 4h | 2,114 | 36% | -33.1% | – | 0.8 | -18% | +93% | +25% | +28% | +68% |
| rotation | 4h | 1,758 | 31% | -74.2% | – | 7.4 | -32% | +132% | +96% | -5% | +21% |
| donchian_ens | 4h | 1,107 | 19% | -53.3% | – | 2.0 | -20% | +99% | +66% | +3% | -18% |
| hot_coin | 4h | 944 | 15% | -48.2% | – | 2.8 | +2% | +8% | +24% | +47% | -7% |
| dca_dip | 4h | 799 | 11% | **-7.5%** | – | 2.1 | +0% | +11% | +23% | +6% | +10% |
| squeeze_tp | 4h | 423 | -4% | -48.4% | – | 1.7 | -23% | +18% | -2% | +18% | -19% |
| bear_breakdown | 4h | 415 | -4% | -49.6% | – | 2.3 | -2% | +20% | -13% | +5% | -23% |
| pullback_rsi | 4h | 337 | -8% | -63.0% | – | 1.7 | -8% | -2% | -37% | -3% | +22% |
| **dca_dip** | **1h** | **4,637** | **73%** | **-25.9%** | – | 11.7 | +22% | +133% | +94% | +50% | +12% |
| ema_st | 1h | 1,268 | 26% | -87.2% | – | 5.0 | +31% | +65% | +165% | -59% | +9% |
| breakout_pyramid | 1h | 680 | 8% | -91.5% | – | 9.4 | -58% | -33% | +121% | -16% | +160% |
| donchian_ens | 1h | 390 | -6% | -79.3% | – | 7.7 | -19% | -18% | +35% | -24% | +14% |
| hot_coin | 1h | 326 | -10% | -80.1% | – | 10.9 | -42% | -26% | +22% | -19% | +53% |
| ema_mom | 1h | 279 | -13% | -72.0% | – | 3.8 | -25% | +41% | +61% | -55% | -28% |
| squeeze_tp | 1h | 138 | -27% | -88.8% | – | 7.0 | -45% | -31% | -36% | +0% | +12% |
| bear_breakdown | 1h | ~10 (bust) | – | -98.1% | – | 9.5 | | | | | |
| pullback_rsi | 1h | ~10 (bust) | – | -98.1% | – | 6.7 | | | | | |
| rotation | 1h | ~10 (bust) | – | -98.3% | – | 28.2 | | | | | |

What this table says:
- On 1h, only `dca_dip` survives 4 years.
- On 4h, the trend strategies make the money but carry −33% to −62% drawdowns on their own.
- `dca_dip` 4h is a low-return, very-low-drawdown diversifier.
- The bear/short strategies never earned their keep. Even the "bear-only" `bear_breakdown` lost money over 4.8 years.

**E. v3.1 options on each preset, 4.8 years**

$500 → end value, with max DD:

| Option | Calm | Balanced | Aggressive |
|---|---|---|---|
| baseline | 1,169 / −13.3% | 3,593 / −33.6% | 6,921 / −45.2% |
| maker entries | 1,151 / −13.0% | 3,537 / −33.0% | 6,774 / −45.2% |
| pump guard 3 ATR | 1,001 / −13.6% | 2,603 / −33.6% | 4,656 / −45.6% |
| **trend slots bull-only** | 1,097 / −12.2% | **3,445 / −22.1%** | **7,082 / −30.0%** |
| + bear_breakdown slot (bear-only) | 1,090 / −13.6% | 3,165 / −32.3% | 5,583 / −44.6% |
| governor: risk × 0.5 in 15% DD | 1,169 / −13.3% (never triggered) | 1,592 / −22.0% | 2,299 / −31.3% |
| risk rules enforce (coin cap 3×, open risk 15%, correlated 3 @ ρ 0.8) | 1,155 / −13.1% | 3,497 / −33.5% | 6,679 / −45.1% |
| BTC breaker 3%/1h (real 1h data) | 1,139 / −13.3% | 3,381 / −33.6% | 6,504 / −45.3% |
| TP ladder 1/2/3R × 25% | 694 / −10.5% | 1,303 / −24.6% | 2,127 / −33.3% |

Boost (base 9,274 / −41.0%):
- governor halve in 15% DD → 2,914 / −31.4%;
- governor × 0.33 after +100% → 2,428 / −41.0%.

Active (base 3,172 / −63.2%):
- pump guard → 2,253 / −64.5%;
- risk rules enforce → 3,169 / −62.8%.

**F. Monte Carlo, daily-block bootstrap (2,000 paths, 5-day blocks) on the long-history curves**

| Curve | Actual max DD | MC median DD | MC 5% worst DD | P(DD ≥ 30%) | P(DD ≥ 50%) | P(fall below ½ start) | P(end below start) | Median end | 5% worst end |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| preset:original | -52.4% | -48.7% | -70.1% | 99.1% | 44.9% | 6.9% | 2.8% | $5,600 | $669 |
| preset:calm | -13.2% | -14.9% | -24.9% | 1.2% | 0.0% | 0.0% | 0.9% | $1,186 | $643 |
| preset:balanced | -33.2% | -32.3% | -49.7% | 60.5% | 4.8% | 0.2% | 0.9% | $3,760 | $879 |
| preset:aggressive | -44.7% | -42.6% | -62.6% | 93.7% | 24.4% | 1.9% | 1.1% | $7,382 | $1,031 |
| preset:boost | -40.5% | -43.3% | -63.7% | 95.4% | 28.2% | 2.6% | 0.7% | $10,179 | $1,219 |
| preset:active | -61.8% | -63.5% | -84.8% | 100.0% | 87.6% | 22.7% | 9.2% | $3,140 | $317 |
| preset:active + tight ladder | -66.1% | -69.8% | -90.3% | 100.0% | 91.9% | 48.9% | 44.3% | $590 | $95 |
| preset:boost_active (mixed) | -40.2% | -42.2% | -62.4% | 92.5% | 24.7% | 2.5% | 0.6% | $7,537 | $1,152 |
| act:DCA1H only 2% | -25.2% | -12.2% | -19.0% | 0.0% | 0.0% | 0.0% | 0.0% | $4,655 | $2,756 |
| act:Active BRK bull-only | -56.2% | -55.3% | -77.3% | 99.9% | 67.1% | 13.1% | 6.8% | $2,896 | $433 |
| bull:balanced | -22.0% | -26.0% | -41.3% | 29.9% | 0.9% | 0.0% | 0.2% | $3,581 | $1,150 |
| bull:aggressive | -29.8% | -34.4% | -52.3% | 72.0% | 7.4% | 0.4% | 0.3% | $7,407 | $1,534 |
| bull:boost | -30.8% | -36.3% | -54.3% | 78.5% | 9.3% | 0.9% | 0.2% | $9,084 | $1,589 |

**Bootstrap caveat:** for DCA1H the bootstrap median drawdown (−12%) is far *smaller* than the actual one (−25%). The real drawdown came from one clustered episode longer than 5 days. Block bootstrap with short blocks under-represents crash clustering, so treat MC drawdowns as optimistic for mean-reversion strategies.

**G. 50/50 side-by-side mixes** (start 2022-09-04, when 1h data begins; so most of the 2022 crash is not included)

| Mix | $500 → | CAGR | Max DD | Worst mo | MC median DD | MC 5% worst DD | P(DD≥30%) | 2022* | 2023 | 2024 | 2025 | 2026 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **bull:balanced + dca1h 2%** (= new *Steady mix*) | 4,015 | 66.6% | **-14.7%** | -7.3% | -11.0% | -17.0% | 0.0% | +11% | +116% | +100% | +46% | +15% |
| bull:boost + dca1h 3% | 11,139 | 113.9% | -23.9% | -12.3% | -16.4% | -25.2% | 1.2% | +18% | +242% | +142% | +75% | +31% |
| bull:boost + dca1h 2% | 6,566 | 87.9% | -20.6% | -7.0% | -18.1% | -28.2% | 2.9% | +11% | +173% | +99% | +56% | +40% |

**H. Walk-forward on the long history** (risk per slot re-optimised every 90 days on the previous 270 days; 16 trials per window; score = Calmar)

| Profile | OOS return (re-optimised) | OOS max DD | Untouched defaults, same windows | Defaults max DD | % windows profitable | WF efficiency |
|---|---:|---:|---:|---:|---:|---:|
| balanced | +411% | -30.8% | +624% | -22.6% | 50% | 0.55 |
| aggressive | +470% | -30.5% | +1485% | -30.0% | 56% | 0.56 |
| bull:balanced | +289% | -30.8% | +466% | -22.5% | 62% | 0.40 |
| active | +772% | -34.7% | +722% | -48.4% | 79% | 1.04 |

- Re-optimising risk every quarter **hurt** every 4h profile, so fixed settings are better there.
- The exception is Active, where it mainly learned to cut risk, which says Active's default 2% is too high for its breakout slot.

### 4.6 Engine vs backtest agreement

- Live engine code run against a simulated exchange over the same candles: **+62.3%** (max DD −15.9%).
- Backtester: **+57.8%** (max DD −17.3%).
- With runner settings: +45.2% vs +52.1%.
- Lots and stops matched the exchange at the end; only DOGE dust was left over.

---

## 5. Open risks and unknowns

1. **Testnet ≠ mainnet:**
   - fills, slippage, funding and the algo-order endpoints differ;
   - the order-lookup-on-timeout path has unit tests but has never fired for real;
   - the algo cancel path was verified live only today (A1).
2. **Maker-entry fill model** is optimistic: it assumes a fill when price trades through. There is no queue position and no partial fills. While a maker order rests (up to 40 s by default) the entry has **no stop**.
3. **Data limits:**
   - 1h history covers 4.1 years, core 8 only;
   - there is no 1m data, so intrabar order is still a heuristic;
   - the long tests have survivorship bias (8 surviving large caps).
4. **In-sample selection:** every preset was picked on data it was then tested on. Walk-forward shows parameter re-tuning does not help. The long history is the closest thing to an out-of-sample check for the 2-year presets, and they held up, with larger drawdowns in 2022.
5. **The funding filter is not backtested**: no historical funding series is loaded. Only a per-bar average funding cost is modelled.
6. **The 4h BTC breaker uses a proxy** (4h move ÷ 2) unless 1h BTC data is supplied. The Backtest tab does not supply it yet; the research script did.
7. **The bot runs on a home PC:** sleep, Windows updates or a network drop stop it. Exchange-side stops protect open positions, but nothing manages them while it is down. Cloud/24-7 is on the Later list.
8. **Single-exchange, single-account**; no kill switch outside the PC except Telegram `/pause` and `/flatten`, once the chat id is fixed.
9. **Grids:** every tested config lost money. They are shipped only as an experiment.
10. **Concentration:** all profiles are long-biased crypto beta. Shorts never worked in testing, so a long bear market is handled by *not trading* (the bull filter), not by profiting from it.

---

## 6. Recommendations (Claude's view)

1. **Swap Active for Active DCA (1h) or Steady mix on the testnet run now.**
   - Over 4 years Active's 1h breakout slot produced −63% drawdowns. That also applies to the half of Boost+Active Moe is running.
   - Steady mix had the best risk-adjusted result of everything tested: −15% max DD and 67% CAGR since late 2022.
2. **Turn on the bull-only filter for trend slots in the higher-risk profiles.**
   - Over 4.8 years it removed nearly all of the 2022 loss and cut drawdowns by about a third at about the same return.
   - The cost is missing early recoveries, as in 2026.
   - Consider making it the default for Aggressive/Boost.
3. **Keep these off:** TP ladder, trailing TP, pump guard, martingale and grids. Each was worse in both data sets.
4. **Maker entries:**
   - Neutral on 4h. Keep them off until the fill model is validated against live testnet fills.
   - On 1h, run a live A/B test before trusting the large backtest gain.
5. **Governor "halve in a 15% drawdown"** reliably cuts drawdowns but costs a lot of return. The bull filter achieves similar drawdown reduction more cheaply. Use the governor as a second layer on Boost only.
6. **Before real money:**
   - 4+ weeks on testnet with the v3.1 build;
   - fix the Telegram chat id;
   - a small mainnet canary ($50–100) with withdrawals disabled on the key;
   - compare live fills with the backtest.

---

## 7. Comparison with paid bots (summary)

| Capability | 3Commas | Cryptohopper | Bitsgap | HaasOnline | Freqtrade (free) | Pionex | **ZackBot v3.1** |
|---|---|---|---|---|---|---|---|
| Price | $20–140/mo | up to ~$107/mo | subscription | $9–149/mo | free / OSS | free (fees) | own |
| Hosted 24/7 | yes | yes | yes | cloud or self | self | yes | **no (home PC)** |
| DCA / grid / COMBO | yes | yes | yes (COMBO futures) | scripts | via strategies | grid-first | yes (grid experimental) |
| Custom strategies | limited | marketplace | presets | HaasScript | Python | no | 10 built-in + slots |
| Optimiser / walk-forward | basic | basic | – | yes | hyperopt / FreqAI | – | **optimiser + WF + MC + lookahead** |
| Portfolio risk rules | limited | limited | limited | scripts | protections | – | **5 rules, warn/enforce** |
| Regime filter / governor | – | – | – | scripts | via code | – | **yes** |
| Telegram control | alerts | alerts | alerts | – | **full** | – | full (with confirm codes) |
| TradingView webhooks | **yes** | yes | yes | yes | via ext. | yes | later |
| Mobile app / web | yes | yes | yes | web | FreqUI | app | Telegram now, web later |
| Copy-trading / marketplace | yes | yes | – | marketplace | – | – | later (Binance lead) |
| Security posture | API keys on vendor servers (3Commas key leaks 2022–23) | vendor | vendor | vendor/self | self | exchange | **keys local, DPAPI-encrypted** |

**Where ZackBot is ahead:**
- research honesty (in- vs out-of-sample badges, block-bootstrap MC, lookahead check);
- risk rules plus regime and governor;
- local key custody.

**Gaps:**
- 24/7 hosting;
- mobile/web control;
- TradingView signals;
- multi-exchange;
- polished distribution (signing, auto-update).

---

## 8. Roadmap: the "Later" list (agreed with Moe)

1. Run 24/7 off the home PC (small cloud VM or Windows service with a watchdog and auto-restart).
2. Full secure web control from mobile, beyond Telegram.
3. TradingView signal bot (Pine alert → webhook → ZackBot sizing and risk).
4. Copy-trading integration (Binance lead-trader portfolio, public stats page / monthly PDF, tax/P&L export).
5. Multiple accounts (a sub-account per profile) and more exchanges (Bybit, OKX).
6. Shareable release: code signing, auto-update, crash reporting, licence keys, setup wizard.
7. Technical:
   - WebSocket user-data and mark streams instead of polling;
   - 1m data for exact intrabar backtests;
   - historical funding series;
   - CI pipeline;
   - pass real 1h BTC data to the breaker in the Backtest tab;
   - clean up legacy config warnings.

---

## 9. Questions for the reviewer

1. **Trend + dip-buyer mix.** Is a 50/50 split of trend-following (4h, regime-filtered) and mean-reversion DCA (1h) a sound core? Should the split be risk-parity instead (by each part's realised volatility) rather than by capital?
2. **Regime filter.** BTC vs its daily EMA200 is crude. What regime definition would hold up better out-of-sample, without becoming another overfit parameter? Options: a 2-of-3 vote (EMA200, 20-week SMA, realised-volatility percentile), or a hysteresis band.
3. **DCA dip-buyer risk.** It looks great in Monte Carlo but had a −25% real cluster. What stress tests would expose its tail better? Candidates: longer bootstrap blocks, replaying the LUNA/FTX weeks, or a synthetic −50% gap.
4. **Maker fills.** What is a realistic way to model post-only fills from OHLCV only (no order book)? Is it worth logging live testnet maker fills to calibrate?
5. **Shorts.** No short strategy worked in 4.8 years on these coins. Is it worth pursuing (funding-arbitrage, basis, or relative-value pairs instead of directional shorts), or should bear markets be handled purely by staying flat?
6. **Going live.** Which live-vs-backtest drift metrics should gate the move from testnet to a mainnet canary, and from the canary to full size?
7. **Survivorship bias.** How should the long test correct for the 8 surviving coins? For example, include delisted or collapsed coins (LUNA, FTT) from 2022 snapshots.
8. **Process.** Anything in the safety model (§2 v3.0) that a professional desk would still consider missing? Examples: an exchange-side kill switch, max-order-rate guard, or a heartbeat dead-man's switch that cancels or flattens if the bot goes silent.
