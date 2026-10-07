# T09a: shorts, profit protection, DCA safety and a regime governor (research and backtest design)

*Draft, 2026-10-07 (Cairo). Branch base: `/tmp/core2` `core-v3` @ `3736774` (master + T05a audit + T06–T09 shared core).
Status: design only. Nothing in the repo is changed. All defaults stay as they are until the owner approves the evidence.*

Owner requirements: PR #8, 2026-10-07. Trigger case: automated long losses clustered near -1R during a 3–4% market drop:
- ETH DCA1H: -1.01R after +3 DCA;
- DOGE DCA1H: -0.98R after +3 DCA;
- 1000PEPE MOM: -1.02R.

Many trades also went green and then finished red.

---

## 0. TL;DR

1. **What exists today.**
   - **Shorts:** five long strategies already compute short signals (`se`), but the registry masks them (`sides='long'`). The only short-only strategy is `bear_breakdown`.
   - **Exits:** most profit-protection mechanics already exist as management keys: `tp1`, ladder, `be_r`, `trail_atr`, `ttp`, `runner` incl. `giveback`, and `max_bars`.
   - **Regime:** there is one BTC-only regime filter (`when`), and the BTC breaker can pause or halve DCA.
   - **Missing:** breakeven *after costs* (`be_r` moves to the exact average), a "no-progress" time stop, regime/reversal exits, per-asset regime, a direction-permission governor, and a trend gate on DCA safety orders.
2. **Exploratory numbers.** These are in-sample, single path and not deflated, so they are no basis for a default change (§7).
   - DCA1H (core 8, 1h, 2022-08→2026-10) reproduces the `active_dca` preset exactly: $500→$4,637, max DD -25.9%.
   - **All 78 losses at ≤ -0.8R come after the full 3 safety orders.** They sum to **-81.4R**, against **+113.7R** net over the whole run.
   - A BTC-bear gate (no new DCA entries while BTC is below its daily EMA200) cuts max DD from -25.9% to -15.3% and the worst month from -13.6% to -9.1%. It also cuts the end value by 30%.
   - **On the trend strategies, every profit-protection variant tested lowered expectancy over 4.8 years.** For example, `ema_mom` went from +0.49R to between +0.13R and +0.38R. Quick-bank variants mainly trade return for lower drawdown.
   - Enabling the existing short signals as they are loses money on 3 of 4 trend strategies. The owner's rule "never enable from one falling day" is supported by the data.
3. **Plan.**
   - First, a research harness with a trial registry and deflated metrics, plus the `tp1_frac=1.0` bug fix.
   - Then shared-core flags, all off by default, with golden parity.
   - Then a governor in Recommend mode.
   - Then one E/D-class PR per feature, each with strict replays and a testnet canary. Shorts go to shadow first, using the existing T05a `side_masked` funnel.

---

## 1. Inventory: what exists today (code facts)

### 1.1 Short logic

| Strategy | Registry `sides` | Short signal it already computes (`se`) | Short exit (`sx`) |
|---|---|---|---|
| `ema_st` | long | EMA50 crosses above EMA20, close < EMA200, Supertrend down | EMA20 > EMA50 or Supertrend up |
| `ema_mom` | long | Same cross, with 7d and 30d returns < 0 | Cross back or momentum gone |
| `donchian_ens` | long | 2 of 3 of the 20/55/100 lows broken | Close > 20-bar high |
| `breakout_pyramid` | long | Close < 20-bar low with EMA50 < EMA200 | none (stop/trail) |
| `squeeze_tp` | long | Squeeze, then close < lower BB, close < EMA200 | none |
| `pullback_rsi` | long | **Pullback short:** downtrend and RSI14 crosses down through 60 | none |
| `bear_breakdown` | **short** | BTC < its EMA200 (on the coin's timeframe, not daily), coin < 20-bar low, EMA20 < EMA50 | 10-bar high, or BTC no longer down |
| `rotation` | long | Bottom-k rank, close < EMA50, `btc_down`, only if `params.shorts` | Rank/EMA50 |
| `dca_dip`, `hot_coin` | long | **none** (`se` is never true) | — |

Plumbing that already works for shorts:
- `sides` can be long, short or both on every sleeve, in both the backtest (`run`) and the engine (`sleeve()`, the signal masks around engine.py:693–701).
- All management math is side-symmetric: `core.levels` uses `side`, and the backtest DCA loop handles `sd == -1`.
- T05a already logs raw signals that a slot's `sides` hide as `side_masked` funnel events (engine.py:1618). **That is a free shadow dataset for shorts.**

**Not present:** range-rejection / mean-reversion short, scalp short, short DCA (that would need its own `se` for `dca_dip`), and 15m data (only 1h and 4h exist in `data/`, `data1h/` and `data_long/`).

### 1.2 Exits and profit protection (mgmt keys, `strategies.py` docstring + `backtest.run` steps 1–6)

| Mechanic | Key(s) | Notes / gaps |
|---|---|---|
| Hard stop | `stop_atr`; DCA `dca.stop_atr` | Always present. `martingale_risk` refuses a DCA basket without a hard stop. |
| Partial TP / Quick Bank | `tp1_r`, `tp1_frac` | **Bug:** `tp1_frac=1.0` keeps a zero-qty position open (backtest.py:349–350: `close()` returns True but the lot is not deleted). This duplicates trade rows. **Fix it before any full-close experiment** (the scratch copy used for §7 does). |
| TP ladder | `tps` (up to 8 `[r, frac]`) | Backtest has no dust rule; live has one (KNOWN_DELTAS 9c). |
| Full TP | `tp_r` | Disabled when `runner` or `ttp` is set. |
| Breakeven | `be_r` | **Moves to the exact `avg`, with no cost buffer** (backtest `CL.ratchet(sd, stop, avg)`; live candidate `avg`). A BE exit therefore loses about 2×fee + 2×slip ≈ 0.14% of notional. `BE_BUF = 0.0015` is used only by runner BE, basket BE and the breaker `tighten`. **"Breakeven after costs" does not exist for `be_r`.** |
| ATR chandelier | `trail_atr` | Active from entry, ratchet-only (the docstring says "once in profit", but the code has no activation threshold). |
| Trailing TP | `ttp` {`at_r`, `dev_pct`} | Percent trail after `at_r`. |
| Runner | `runner` {`be_r`, `step_r`, `gap_r`, `giveback`, `gb_from`, `dca_frac`, `trend_exit`} | `giveback` + `gb_from` **is an MFE give-back trail** (lock = bestR × (1 − g) from `gb_from`). Side effects of `runner`: it disables `tp_r` and `max_bars`, and suppresses signal exits on winners in a healthy trend. **A pure give-back policy needs its own flag** so the experiment is not confounded. |
| Time exit | `max_bars` | Unconditional only. **No "no-progress" stop** (for example, exit if MFE < x R after N bars). |
| Signal exit | `lx`/`sx` at close | Strategy-specific. **No regime/reversal exit** for losers. `runner.trend_exit` only covers winners. |
| DCA basket TP | `dca.tp_atr` (+ runner `dca_frac` bank) | Basket TP = avg + tp_atr × ATR at entry. |

### 1.3 Regime gates, breakers and governor

| What | Where | Scope |
|---|---|---|
| `regime()` bull/bear/range | `strategies.regime` | **BTC only.** Bull/bear = last *completed* daily close vs daily EMA200 (available at +1 day, min 30 days). Range = 4h ADX < 20 and BB-width percentile < 0.35. Shared by engine (`regime_now`) and backtest. Lookahead test: `lab.regime_lookahead`. |
| Sleeve `when` | backtest `entry_filters`, engine ~1684 | Blocks **new entries** of a sleeve outside `bull`/`bear`/`range`. Does not touch open lots or adds. `steady_mix` uses `when='bull'` on MOM/ST. |
| BTC breaker | `risk_rules.btc_breaker` {pct, hours, tighten, dca: pause\|half_size} | Fires on BTC's 1h move > pct. Pauses entries and pyramids. DCA policy is fixed per basket at entry (engine:2152). **Default mode is `warn`.** Reacts to shocks, not to trend. |
| Governor | `governor.rules` (growth/DD → `risk_mult`) | Equity-curve based only. No direction or asset awareness. |
| Pump guard, coin/open-risk/correlated caps | `pump_guard`, `risk_rules` | Not regime-aware. |

**Missing:**
- per-asset regime;
- long/short/neutral permission per asset and timeframe;
- a trend-aware gate on DCA safety orders;
- breadth (share of the universe in a downtrend);
- Manual / Recommend / Automatic modes for direction.

### 1.4 Costs and their known biases (relevant to shorts and to quick TPs)

- `core.costs.BACKTEST`: taker 0.05%, maker 0.02%, slippage 0.02% per side, funding 0.005% per **bar**.
  - Callers must scale funding by timeframe (`fund_per_bar = FUND_PER_BAR × tf_sec / 14400`). `lab`, `app` and the research scripts do this; a raw `run()` on a 1h book overcharges funding 4×.
- **Funding is always a cost, on both sides.** On real Binance, positive funding is *paid to* shorts. The model is therefore pessimistic for shorts in bull markets and optimistic for shorts in squeezes. A short evaluation needs historical funding rates (`/fapi/v1/fundingRate`, point in time; see O3).
- One round trip costs ≈ 0.14% of notional (2×0.05% fees + 2×0.02% slippage) plus funding. A TP of 0.5R on a 2.5-ATR stop is about 1.2 ATR. That is fine on 4h, but on 1h low-volatility coins it can be well under 10× the cost (§3.3 cost floor).

### 1.5 Audit failure classes (T05a / PR #8)

`green_to_red`, `gave_back_gt_50pct_mfe`, `never_green`, `dca_into_trend`, `long_in_bear_regime` and `missed_short_opportunity` are **not on `core-v3` yet**: `grep` finds none of them in `trade_audit.py`. They arrive with the PR #8 audit work.

T09a uses the **same definitions** in its backtest metrics (§4.6), so live audits and research report the same classes.

### 1.6 Walk-forward and validation tooling that already exists (`lab.py`)

| Tool | What it does |
|---|---|
| `optimize` | Search on the first 75%, report the top-k on the untouched last 25% (`HOLDOUT_FRAC`) |
| `walk_forward` | Rolling train/test/step, chained out-of-sample curve, WF efficiency, baseline OOS curve |
| `monte_carlo`, `monte_carlo_daily` | Trade shuffle and daily-block bootstrap |
| `lookahead_check`, `regime_lookahead` | Truncation tests |
| `tests/test_causality.py` | Engine perturbation, backtest-vs-causal-engine parity, same-candle information detector |

**Not present:**
- a trial registry (how many variants were tried);
- deflated Sharpe / PBO;
- per-regime breakdown;
- give-back statistics;
- R-expectancy confidence intervals.

---

## 2. Data available (from `DATA_MANIFEST.json`)

| Set | Coins | Timeframe | Span | Use |
|---|---|---|---|---|
| `data_long/4h` | core 8 | 4h | 2021-12-19 → 2026-10-04 (4.8 years, includes the 2022 bear) | Primary walk-forward for 4h strategies |
| `data_long/1h` | core 8 | 1h | 2022-08-26 → 2026-10-04 (4.1 years) | Primary walk-forward for DCA1H, BRK1H and 1h shorts |
| `data/` | 40 | 4h | 2024-09-14 → 2026-10-04 (2 years) | Breadth / robustness only. Survivorship-biased (no LUNA/FTT). PEPE-type coins live here. |
| `data1h/` | 7 | 1h | about 6 months | Not used for decisions |

Caveats that cap the evidence score (Phase 3 hard caps):
- the universe is today's survivors;
- no point-in-time funding;
- no 15m data;
- **the last 6 months (2026-04→2026-10) were already used to choose presets.** No local data is truly untouched. A genuine holdout must come from the future (§4.2).

---

## 3. Experiment matrix

Each experiment cell is **strategy × side × timeframe × regime policy × exit policy × DCA gate**. Universe and risk stay fixed: core 8, `risk=0.02`, `max_pos` as in the presets, `max_lev=10`, `daily_halt=0.08`, $500 start, taker entries. Maker entries are a separate execution axis that is not mixed in.

### 3.1 Signal candidates (S)

All candidates are causal: signals are computed on closed candles and filled at the next open. Every new candidate gets its **symmetric long version**, evaluated alongside it under the same protocol.

| ID | Family | Short definition | Long mirror | New code? |
|---|---|---|---|---|
| S1a | Trend breakdown | `bear_breakdown` as it is | — (short-only design) | no |
| S1b | Trend breakdown | `donchian_ens` `se` (2 of 3 channel lows) | `donchian_ens` `le` | no (sides) |
| S1c | Momentum short | `ema_mom` `se` | `ema_mom` `le` | no (sides) |
| S1d | Breakdown + pyramid | `breakout_pyramid` `se` | `le` | no (sides) |
| S2a | Pullback short | `pullback_rsi` `se`: EMA50 < EMA200, close < EMA200, RSI14 crosses down through 60 | `le` | no (sides) |
| S2b | Pullback short (EMA rejection) | Downtrend (EMA50 < EMA200), high[i] ≥ EMA20[i−1] and close < EMA20, close < open, ADX ≥ 20. Stop = max(high of last 3 bars) + 0.5 ATR, floored at 1.5 ATR. | Mirror | yes (`sig_ema_reject`) |
| S3a | Range rejection | BTC `range` regime **or** coin ADX < 20. Prior bar closed > `bbu`, this bar closes back < `bbu`, RSI14 > 65. Target `bbm` (tp at about 1R), stop = swing high + 0.5 ATR. `max_bars` 12 (4h) / 24 (1h). | Mirror at `bbl` | yes (`sig_range_reject`) |
| S3b | Mean-reversion short (RSI2) | close < EMA200, RSI2 > 90, close > EMA5. Exit at close < EMA5 or after 5 bars. Stop 2 ATR. | RSI2 < 10 above EMA200 (Connors) | yes |
| S4 | High-risk scalp short | 1h (15m only after data exists): `vol_ratio` > 2, close < `ll10`, BTC 1h move < −1%, stop 1 ATR, tp1 50% @ 1R, trail 1.5 ATR, `max_bars` 6. Gated to `aggressive` style only. | Mirror (`hot_coin`-like) | yes |
| S5 | Short DCA (research only) | `dca_dip` mirror: stretch above EMA20 > 1.5 ATR in a downtrend, safety orders upward | `dca_dip` | yes; lowest priority (squeeze risk) |

Rules for every short candidate:
- **The short side is always tested under three regime policies:**
  - P0: ungated;
  - P1: BTC bear (`when='bear'`, daily EMA200, confirmed);
  - P2: governor (§3.4).
- **No short sleeve is evaluated on a window under 180 days** or on a single drawdown event. "Never enable from one falling day" is enforced by the governor's confirmation (§3.4) and by the acceptance criteria (§5).

### 3.2 Regime states (R): causal decision labels vs report labels

Decision labels must be computable at the decision bar from closed data only. They are computed in the shared `strategies.regime` style, with `merge_asof` on availability time.

| Label | Definition | Availability |
|---|---|---|
| `btc_bull` / `btc_bear` | BTC daily close vs daily EMA200 (existing) | Next day 00:00 UTC |
| `btc_bear_conf` | `btc_bear` for ≥ 3 consecutive daily closes **and** the daily EMA200 slope over 10 days ≤ 0 | Next day |
| `coin_trend` | Coin close vs its own EMA200 on the trading timeframe, plus EMA50 vs EMA200 | Bar close |
| `coin_daily_trend` | Coin daily close vs daily EMA200 (resampled like `regime()`) | Next day |
| `breadth_bear` | Share of the universe with daily close < daily EMA200 ≥ 60% (point in time; only coins with ≥ 200 days of history) | Next day |
| `range` | Existing 4h ADX/BBW rule | 4h close |
| `shock` | Existing BTC 1h move > pct (breaker) | 1h close |

**Report labels** (for the per-regime breakdown only, and never fed into a decision):
- `ex_post_regime`: a centred 60-day trend label, named differently on purpose;
- the decision label at entry.

Both appear in reports so that label leakage is visible.

### 3.3 Exit / profit-protection policies (E)

Notation: R = initial stop distance; MFE = best favourable excursion in R since entry (causal running max of bar extremes); `rt_cost` = 2×fee + 2×slip + expected funding over the median hold, as a fraction of price.

**Cost floor (applies to E1, E2 and E7):** a partial-TP level is admissible only if `tp1_r × R / price ≥ 4 × rt_cost`, judged at entry. Otherwise the policy falls back to E0 for that trade and logs it as `cost_floor`. This is how "no universal tiny TP that turns expectancy negative through fees" is enforced mechanically.

| ID | Policy | Exact definition | Grid (pre-registered) | Existing key / new flag |
|---|---|---|---|---|
| E0 | Baseline | Strategy defaults | — | — |
| E1 | Quick Bank | Close `f` at `a` R, rest unchanged | a ∈ {0.5, 0.75, 1.0}, f ∈ {0.25, 0.33, 0.5} | `tp1_r`/`tp1_frac` |
| E2 | Breakeven after costs | Once MFE ≥ b R, stop → avg × (1 + side × (rt_cost + funding paid so far / notional)) | b ∈ {0.75, 1.0, 1.5} | **new** `be_cost=True` (today `be_r` = exact avg) |
| E3 | ATR chandelier, activated | Stop = best − side × k × ATR[i−1], active only once MFE ≥ act R | k ∈ {2, 3, 4}, act ∈ {0, 1, 2} | `trail_atr` + **new** `trail_from_r` |
| E4 | MFE give-back trail | Once MFE ≥ a R: stop ≥ e0 + side × (1 − g) × MFE × R | a ∈ {1, 1.5, 2, 3}, g ∈ {0.33, 0.5, 0.66} | **new** pure `giveback` flag. `runner.giveback/gb_from` exists but drags in runner side effects (§1.2). |
| E5 | No-progress time stop | Exit at close if bars ≥ N and MFE < x R | 4h: N ∈ {6, 12, 24}; 1h: N ∈ {12, 24, 48}; x ∈ {0.25, 0.5} | **new** `stall` {n, mfe_r} (`max_bars` is unconditional) |
| E6 | Reversal / regime exit | Exit at close when (long) Supertrend flips down **and** close < EMA50, **or** the BTC decision regime flips to `btc_bear_conf`. Mirror for shorts. Winners and losers alike. | variants: ST-only, regime-only, both | **new** `rev_exit` |
| E7 | Runner split | E1 with f ∈ {0.33, 0.5}, then the remainder on E3 (k = 3) or E4 (g = 0.5) | 4 combos | combination |
| E8 | Fixed ladder | `tps` [[1, .25], [2, .25], [3, .25]] + E3 on the rest | 2 ladders | `tps` |
| E9 | DCA-specific | Basket TP `tp_atr` ∈ {0.75, 1.0, 1.5}; runner `dca_frac` ∈ {0.5, 1.0} | 6 | existing |

Selection rule: policies are chosen **per strategy × regime bucket** (bull / bear / range) inside each walk-forward train window. Only the policy family and its grid are pre-registered; the winning parameters must be stable across windows (§5).

### 3.4 DCA safety-order gates (D)

The hard basket stop is kept in every cell. DCA exists to improve the risk-adjusted R of a dip entry, **not to raise the win rate**. The win rate is reported, but it is never an objective.

| ID | Gate (applies to safety order k ≥ 1; decision on bar i−1 closes) | Parameters | New code? |
|---|---|---|---|
| D0 | none (today) | — | — |
| D1 | `btc_bear` → pause further safety orders | — | **new** `dca_gate` |
| D2 | `btc_bear` → halve further safety orders | — | new |
| D3 | `coin_trend` against the basket (coin < EMA200, timeframe of the sleeve) → pause / halve | pause, half | new |
| D4 | Reversal confirmation: safety order k is armed when its level is touched and fills only after a bar closes back on the favourable side of the level (or RSI2 crosses back through 10). It is filled at the next open, and cancelled if the hard stop comes first. | confirm ∈ {close_back, rsi2} | new (delayed fill) |
| D5 | Basket caps: max safety orders in `btc_bear` = n_bear; basket time cap T bars after the last add; stop tighter after the last add | n_bear ∈ {0, 1, 2}; T ∈ {24, 48} (1h); `dca.stop_atr` ∈ {1.0, 1.5, 2.0} | partly existing (`max_bars`, `stop_atr`), n_bear new |
| D6 | Entry gate: no new baskets in `btc_bear` (existing `when='bull'`) or in `breadth_bear` | — | existing / breadth new |

Rules:
- A gated safety order is **recorded** (`dca_gate_block`, reason code), never silently dropped.
- The basket's risk accounting (`lot_risk` incl. unfilled levels) stays unchanged. Pausing adds can only lower risk.
- The policy is fixed at basket entry, mirroring today's breaker rule (engine:2152), so a regime change mid-basket is deterministic and replayable.

### 3.5 Regime governor (G)

A pure function `core/regime_gov.permissions(asset, tf, ctx) -> {long: allow|reduce|block, short: allow|reduce|block, mult_long, mult_short, reasons}`.

| State (decision labels) | Long | Short (only on sleeves the user set to `both`/`short`) |
|---|---|---|
| `btc_bull` and coin trend up | allow (1.0) | block |
| `btc_bull` and coin trend down | reduce (0.5) | block |
| `range` | allow (mean-reversion sleeves only), trend longs reduce 0.5 | allow S3 only |
| `btc_bear` not confirmed | reduce (0.5) for trend; DCA per D-gate | block (still neutral) |
| `btc_bear_conf` or `breadth_bear` | block new trend longs, DCA per D-gate | allow / reduce per evidence |
| unknown (short history, stale data) | block (fail-closed, like `when` today) | block |

Hysteresis:
- a state change needs **≥ 3 consecutive daily closes**;
- a minimum dwell of 5 days before flipping back;
- the `shock` state only ever reduces.

That makes "enable shorts from one falling day" structurally impossible.

User control (one setting per account, then per sleeve override):

| Mode | Behaviour |
|---|---|
| Manual | The governor computes and displays only. The user's `sides`/`when` rule. |
| Recommend | Same, plus "Why?" text and a Telegram notice on state changes; audit records `would_block` / `would_reduce`. |
| Automatic | Applied. **It may only narrow what the user allowed** (decision rule B4): it can block or reduce longs and enable shorts only on sleeves the user opted into with `sides='both'`. It never raises size above 1×. Open lots are never force-closed by the governor; E6 is a separate exit policy. |

Default after T09a: **Manual**, so there is no behaviour change.

### 3.6 Cell budget (to keep the multiple-testing burden honest)

Not a full factorial. Stages:

| Stage | Question | Cells |
|---|---|---|
| A — signal screen | S-candidates × {long, short} × P0/P1/P2 × E0 | ~11 × 2 × 3 = 66 |
| B — exits | survivors of A (≤ 6) × E1–E8 grid | ≤ 6 × ~45 = 270 |
| C — DCA | `dca_dip` 1h and 4h × D0–D6 grid × E9 | ~2 × 20 = 40 |
| D — governor | the combined profile (`steady_mix`, `active_dca`, `balanced`) with G Manual vs Automatic, plus the B/C winners | ~12 |

Total ≈ 400 trials, all logged in the trial registry. The deflation (§4.5) uses the actual count.

---

## 4. Evaluation protocol

### 4.1 Engine and costs

- Use `backtest.run` through `lab.call_run` only, after the `tp1_frac` fix. New policies go through `core.levels` / `core.state` flags so the engine and backtest share them.
- **Costs:**
  - preset `core.costs.BACKTEST`, with `fund_per_bar` scaled by timeframe;
  - plus a **stress run** at 2× slippage and 2× funding;
  - plus a **real-funding run** once O3 is answered: sign-correct funding, so shorts receive in positive funding.
- **Fills:**
  - `pessimistic='path'` as the default; `'worst'` as the stress run for every exit policy, since trailing policies are the most sensitive to intrabar order;
  - E-policies whose result flips sign under `'worst'` are rejected.
- **1h-only check:** for 4h strategies, re-run exits on 1h-resolution paths (data_long 1h exists for core 8) to measure the intrabar bias of trail/BE policies.

### 4.2 Splits

| Window | 4h (core 8) | 1h (core 8) |
|---|---|---|
| Warm-up | 220 bars (+200 daily bars for the EMA200 regime ⇒ effectively from 2022-07) | from 2022-08 + 220 bars |
| Walk-forward | Rolling train 365 d / test 90 d / step 90 d (`lab.walk_forward`, ~12 windows); chained OOS curve | same |
| Regime breadth | Report each OOS window tagged by the decision regime mix; require ≥ 1 OOS window in each of bull, bear and range | same |
| Breadth set | `data/` 40 coins 2024-09→2026-10: OOS-only confirmation (no fitting) | — |
| **Forward holdout** | Definitions frozen (hash in the registry) at T09a-H merge. Data after 2026-10-04 (`research_refresh.py`) plus the testnet shadow from that date is the only truly untouched sample. **Minimum 90 days** before any default changes. | same |

### 4.3 Minimum sample

- **≥ 100 closed trades** OOS per cell (DCA adds don't count as trades; Phase 3 rule), with ≥ 30 in each regime bucket being judged.
- Shorts: ≥ 50 OOS trades in `btc_bear` windows from **at least two separate bear episodes** (2022 and the 2025–26 drawdowns).
- Fewer trades means a hard cap of "insufficient evidence"; the cell can't change a default.

### 4.4 Metrics per cell (all OOS, all from the same trade table)

| Group | Metric |
|---|---|
| Expectancy | mean R (per trade, using `R_BACKTEST` = realized / risk incl. all costs), median R, **95% CI by stationary block bootstrap** (block = 20 trades), USD expectancy, sum R |
| Curve | CAGR, max DD, worst month, Calmar, daily Sharpe, time under water, MC daily-block 5th percentile end and P(DD > 30%) via `lab.monte_carlo_daily` |
| Tail | Share and sum of trades ≤ −0.8R; worst 5 trades; **near −1R cluster count per calendar day** (directly targets the reported incident) |
| Give-back | % trades with MFE ≥ 0.5R and ≥ 1R that close red (`green_to_red`); % closing with < 50% of MFE (`gave_back_gt_50pct_mfe`); `never_green` %; mean MFE captured (R_final / MFE) |
| DCA | Distribution of the number of safety orders filled; mean R by count; `dca_into_trend` count (adds filled while the D-label is against); worst basket R; max basket notional / equity (`martingale_risk`) |
| Direction | Long vs short split; `long_in_bear_regime` share of losses; `missed_short_opportunity` (S1–S3 signals blocked by `sides` that would have reached +1R first: hindsight, labelled) |
| Costs | Fees + slippage + funding as % of gross P&L; **cost drag per policy** (E1/E2 must report how much of the R change is fees) |
| Copy suitability | Orders per trade, % orders < 10/50/100 USDT follower minimum (Phase 3) |

### 4.5 Multiple-testing guard

- **Trial registry** (`research/t09a_trials.jsonl`): one line per run with config hash, data manifest hash, code commit, split, metrics, and `stage`. The registry is append-only and count-checked by a test.
- **Deflated Sharpe ratio** (Bailey & López de Prado 2014), using N = trials in that stage and the variance of the trial Sharpes. A candidate needs **DSR ≥ 0.95**.
- **PBO via CSCV** (16 blocks) for each stage's selection: PBO must be **≤ 0.2**.
- **Holm–Bonferroni** on the R-expectancy improvement vs baseline (paired block bootstrap on aligned trade/days), α = 0.05 family-wise per stage.
- **Parameter stability:** the chosen parameter must sit on a plateau. The neighbouring grid points must keep ≥ 70% of the improvement; spikes are rejected.

### 4.6 Baseline comparisons

Every candidate is compared to:
1. **long-only today** (same sleeve, E0, D0);
2. **long-only + the simplest gate** (`when='bull'`), because a complex policy must beat the trivial one;
3. for shorts, a **cash** sleeve with the same capital share (a short sleeve that only adds DD at zero expectancy is worse than cash).

Results are reported **per profile** (`active_dca`, `steady_mix`, `balanced`) at the portfolio level, since correlation with the long sleeves is the main argument for shorts.

---

## 5. Acceptance criteria to change a default

All must hold OOS (walk-forward chained), at portfolio level for the target profile, and again on the forward holdout:

1. **Expectancy:** mean R not lower than the baseline by more than its 95% CI half-width, **and** either
   - (a) mean R improves with Holm-adjusted p < 0.05, or
   - (b) max DD and worst month improve by ≥ 20% relative while CAGR / maxDD (Calmar) does not drop.

   Pure-protection policies are judged on (b) and must keep mean R > 0 after costs **in every regime bucket that they trade**.
2. **Robustness:**
   - DSR ≥ 0.95, PBO ≤ 0.2;
   - parameter plateau (§4.5);
   - sign holds under 2× costs and under `pessimistic='worst'`;
   - holds on the 40-coin breadth set (same sign of the improvement).
3. **Regime breadth:** no regime bucket with ≥ 30 trades gets worse by more than 0.1R. Shorts need positive mean R in ≥ 2 separate bear episodes.
4. **Sample:** §4.3 minimums.
5. **Give-back target (for protection policies):** `green_to_red` at MFE ≥ 1R falls by ≥ 30% **without** mean R falling (criterion 1).
6. **DCA:**
   - the number of ≤ −0.8R basket losses per bear episode falls;
   - worst basket R ≤ 1.05R;
   - and **win rate is never used as the justification.**
7. **Causality:** `lab.lookahead_check` and `regime_lookahead` are clean for every new signal and label, and `test_causality` parity is green with the flag on.
8. **Forward holdout ≥ 90 days**, plus the testnet canary (§6) without a protection or reconcile incident.
9. **Owner approval** recorded on the PR. Codex review has no open findings.

**Kill rule.** A feature that fails 1–3 in walk-forward is dropped. It is not re-tuned on the same data. Re-tests need new data or a pre-registered new hypothesis, which is counted in the registry.

---

## 6. How results feed the Phase 3 scorecard (T13)

| Scorecard component (weight) | T09a input |
|---|---|
| Risk / tail (25) | Max DD, worst month, MC P(DD > 30%), near −1R cluster per day, worst basket R, `martingale_risk` |
| Robustness (25) | WF efficiency, DSR, PBO, plateau score, per-regime consistency, breadth-set agreement |
| Return quality (15) | OOS mean R with CI, Calmar, % of P&L from the top 5% of trades |
| Execution realism (15) | Cost drag %, 2× cost and `worst`-path deltas, real-funding delta, fill-telemetry calibration (T05) |
| Diversification (10) | Correlation of the short or gated sleeve with the long book; DD overlap |
| Data quality (10) | Survivorship flag, funding point-in-time flag, 15m availability |

Hard caps triggered by T09a:
- fewer than 100 OOS trades;
- no forward holdout;
- no point-in-time funding for shorts;
- any lookahead finding.

Readiness and profitability stay separate scores. A strong T09a result **never** flips a setting automatically (Phase 3 rule).

---

## 7. Exploratory sanity backtests (2026-10-07). Not evidence.

**Labels:** in-sample, full period, single path, ~45 variants tried, no deflation, survivorship-biased core 8.

**Method:**
- Run on a **scratch copy** of `backtest.py` (the repo is untouched). The patches are:
  - `dca_gate` for safety orders, decided on bar i−1 closes;
  - the `tp1_frac=1.0` fix;
  - extra trade columns (`dca`, `mfe_r`).
- The copy reproduces the `active_dca` preset note exactly ($4,637 / −25.9% / −13.6%), so baseline parity holds.
- `mfe_r` excludes the exit bar's extreme.
- The g≥1R→red metric is meaningless for DCA, whose R is the full-basket risk.
- Scripts: `scratchpad/t09a/{make_patched.py, run1.py, run2.py, run3.py}`.

### 7.1 DCA1H (`dca_dip`, 1h, core 8, 2% risk, max 4, 2022-08-26 → 2026-10-04, funding scaled to 1h)

| Variant | End $ | Max DD | Worst month | Trades | Win % | PF | Mean R |
|---|---:|---:|---:|---:|---:|---:|---:|
| **Baseline (today)** | 4,637 | −25.9% | −13.6% | 2,491 | 94.1 | 1.75 | +0.046 |
| D1 BTC-bear: pause safety orders | 2,803 | −15.3% | −8.8% | 2,391 | 90.8 | 1.66 | +0.037 |
| D2 BTC-bear: half safety orders | 3,639 | −17.4% | −8.2% | 2,458 | 93.7 | 1.67 | +0.041 |
| D3 coin < EMA200 (1h): pause | 930 | −13.5% | −7.0% | 2,227 | 84.6 | 1.36 | +0.014 |
| D3 coin < EMA200 (1h): half | 2,407 | −19.3% | −9.7% | 2,429 | 92.9 | 1.68 | +0.033 |
| D6 entries only in BTC bull | 3,233 | −15.3% | −9.1% | 1,905 | 94.6 | 2.04 | +0.050 |
| D6 + D1 | 3,180 | −14.9% | −8.7% | 1,905 | 94.5 | 2.05 | +0.049 |
| D5 time cap 24 bars | 4,945 | −21.1% | −11.8% | 2,509 | 92.5 | 1.94 | +0.046 |
| D5 basket stop 1.0 ATR (vs 2.0) | 11,149 | −24.8% | −13.1% | 2,515 | 91.9 | 1.73 | +0.063 |
| n = 2 safety orders | 5,459 | −28.4% | −13.7% | 2,492 | 90.2 | 1.43 | +0.050 |
| n = 1 safety order | 352 | −67.8% | −21.1% | 2,466 | 79.9 | 0.97 | −0.002 |

Loss anatomy (baseline):
- **all 78 trades with R ≤ −0.8 had 3 safety orders filled.** They sum to −81.4R against +113.7R net, so the "−1R after +3 DCA" pattern in the incident is structural and not bad luck.
- Mean R by number of safety orders: 0: +0.027, 1: +0.081, 2: +0.134, **3: −0.072**.
- By BTC regime at entry: bull +0.050R (1,909 trades), bear +0.030R (563 trades). Bear entries are still positive on average, but they carry a disproportionate share of DD.

Reading:
- The **entry gate D6 has the best risk/return** (Calmar ≈ 546% / 15.3 ≈ 36 vs 827 / 25.9 ≈ 32), with the highest PF and mean R. The cost is about 30% lower end equity.
- Coin-level EMA200 gating on 1h is too twitchy: it pauses adds right when they would recover.
- The tighter basket stop and the time cap look attractive, but note:
  - the 1.0-ATR stop **raises notional per unit of risk** (more leverage on the same risk budget) — it needs the leverage/liquidation report and copy-suitability before it is taken seriously;
  - both are exactly the kind of single-parameter in-sample win that §4.5 exists to deflate.

On 4h (`dca_dip`, core 8, 2021-12→2026-10):
- baseline $872, DD −7.5%;
- BTC-bear pause $707, DD −8.3%;
- coin-EMA200 pause $592, DD −6.7%;
- bull-only entries $744, DD −7.6%.

**No gate helps on 4h.** Policies must be chosen per timeframe.

### 7.2 Existing short signals (4h, core 8, 2021-12 → 2026-10, `sides` switched only)

| Strategy | Long only: end $ / DD / mean R | Both | Short only | Short with `when='bear'` |
|---|---|---|---|---|
| `ema_mom` | 2,134 / −34% / +0.49R | 1,283 / −49% / +0.19R | 298 / −55% / −0.09R | 311 / −53% / −0.15R |
| `ema_st` | 4,634 / −50% / +0.64R | 501 / −68% / +0.09R | 156 / −80% / −0.13R | — |
| `donchian_ens` | 656 / −62% / +0.09R | 1,000 / −67% / +0.09R | 669 / −53% / +0.08R | 306 / −66% / −0.04R |
| `breakout_pyramid` | 1,163 / −71% / +0.17R | 2,105 / −85% / +0.21R | 779 / −67% / +0.22R | 46 / −94% / −0.17R |
| `bear_breakdown` (short) | — | — | 574 / −61% / +0.10R (PF 1.02) | built-in BTC gate |

Additional rows:
- `ema_mom` long-bull + short-bear in two half-share sleeves: $900, DD −27%;
- `ema_mom` long-only at half share: $1,190, DD −18.5%.

**Adding the short sleeve made it worse.**

Reading:
- No existing short is close to acceptable.
- Ungated breakdown shorts show a small positive mean R, but with ruinous drawdowns.
- The naive BTC-bear gate makes them *worse*: shorting confirmed bear trends on 4h breakouts gets squeezed.
- This supports the owner's instinct: research new short families (S2b, S3, S4) under the full protocol, and do not just flip `sides`.

### 7.3 Profit protection on trend longs (4h, core 8, 2021-12 → 2026-10)

| Policy | `ema_st` end $ / DD / mean R / green→red (MFE≥1R) | `ema_mom` end $ / DD / mean R / green→red |
|---|---|---|
| E0 baseline | 4,634 / −49.7% / +0.64 / 15.3% | 2,134 / −34.0% / +0.49 / 11.3% |
| `be_r` 1 (exact avg) | 4,028 / −50.1% / +0.57 / 22.2% | 1,760 / −34.6% / +0.38 / 17.6% |
| tp1 30% @ 1R + BE @ 1R | 2,541 / −45.5% / +0.40 / 0% | 1,336 / −30.8% / +0.28 / 0% |
| Quick Bank 50% @ 0.5R | 2,006 / −36.4% / +0.32 / 10.5% | 1,155 / −23.6% / +0.25 / 6.8% |
| Chandelier 3 ATR (from entry) | 993 / −47.3% / +0.15 / 6.3% | 796 / −27.1% / +0.13 / 5.4% |
| Runner (be 2, step 1, gap 1, gb 0.5 @ 3R) | 1,321 / −41.5% / +0.22 / 13.2% | 1,125 / −28.8% / +0.24 / 9.5% |
| TTP 2R / 3% | 1,234 / −40.2% / +0.21 / 13.2% | 1,027 / −26.9% / +0.21 / 8.6% |
| Full TP 100% @ 1R (bug-fixed) | 493 / −39.2% / +0.02 / 0% | 597 / −29.3% / +0.05 / 0% |

Reading:
- On the 4h trend sleeves, **every protection rule cuts expectancy**, because the strategies earn their edge from a fat right tail.
- "Green→red" falls, but mean R falls more.
- A full TP at 1R turns them roughly break-even after fees. That is the "universal tiny TP" failure mode the owner warned about, now measured.
- Quick Bank at 0.5R is the only variant with a clear DD benefit (−34% → −24% on MOM). It is a candidate for criterion 5(b) on DD-sensitive profiles (masters / copy), not as a general default.
- BE @ 1R at the exact average *raises* green→red: trades get stopped at the average after fees and are counted red. That argues for E2 (BE after costs) or no BE at all.

Next step (for the harness, not done here): the same table on DCA1H with E1/E2/E5, plus 1h-resolution path checks.

---

## 8. Implementation plan (PRs)

Order follows the roadmap rules: one behaviour family per PR, never refactor + behaviour, flags default off, strict replays unchanged with flags off.

| PR | Ticket | Class | Content | Evidence to merge |
|---|---|---|---|---|
| 1 | **T09a-0 backtest fix** | D (backtest-only) | `tp1_frac=1.0` (and a ladder/tp1 combination that empties the lot): delete the lot when `close()` returns True at the tp1 step. Add a test proving no duplicate trade rows and no zero-qty lot. The engine already finishes empty lots (`FinishIfEmpty`). | Unit test; golden/replays unchanged for every preset (none uses `tp1_frac=1`); KNOWN_DELTAS note |
| 2 | **T09a-H research harness** | No behaviour change | `research_t09a.py` (stages A–D, multiprocess, < 2 min per job chunk), `research/metrics_t09a.py` (R-CI bootstrap, DSR, PBO/CSCV, Holm, give-back classes shared with `trade_audit` definitions, per-regime tables), append-only trial registry, frozen manifest hash. Backtest gains only **optional extra trade columns** (MFE/MAE in R, adds count, regime at entry), behind a flag. | Unit tests for the metrics on synthetic data; registry test; zero replay change |
| 3 | **T09a-L causal labels** | C (inputs only) | `strategies.regime_labels()`: `btc_bear_conf`, `coin_trend`, `coin_daily_trend`, `breadth_bear`, built with the same `merge_asof` availability rule; added to `lab.regime_lookahead` / `lookahead_check`. | Truncation tests clean; perturbation test in `test_causality` extended |
| 4 | **T09a-F core flags (all off)** | D | `core.levels` / `core.state`: `be_cost`, `trail_from_r`, pure `giveback`, `stall`, `rev_exit`, `dca_gate` {kind, policy, n_bear, confirm}. Engine and backtest both call them; reason codes (`dca_gate_block`, `stall_exit`, `rev_exit`, `be_cost_move`, `cost_floor`) in `core/reasons.py`; settings validation + migration; panel shows them as hidden/advanced. | Golden parity: bit-identical with flags off; per-flag strict replay scenarios (gap through a gated add, stall at a restart, rev_exit on a stale regime ⇒ fail-closed); KNOWN_DELTAS updated |
| 5 | **T09a-G governor** | B (policy) + observe | `core/regime_gov.py` pure permissions + hysteresis; engine computes it per cycle; **Manual default**, Recommend writes `would_block`/`would_reduce` funnel events (T05a) and a "Why?" line; Automatic implemented but locked behind the owner switch, and only narrows. | Validation/round-trip/audit tests; replay with Automatic on a scenario proves new longs are blocked and no open lot is touched; UI Playwright screenshot |
| 6 | **T09a-S short candidates** | C | `sig_ema_reject`, `sig_range_reject`, `sig_rsi2_mr`, `sig_scalp_short` (+ long mirrors) registered with `sides='long'` masked by default ⇒ live runs them as **shadow** through the existing `side_masked` funnel. | Lookahead check, walk-forward report from the harness, ≥ 90-day shadow funnel stats before any enabling |
| 7+ | **T09a-E\<n\> enable per feature** | E (live behaviour) | One PR per (feature × profile) that passed §5, e.g. "active_dca: D6 + D2", "steady_mix MOM: Quick Bank 50%@0.5R on master profile only". Changes the **preset default** only; user settings are never rewritten. | Harness report + registry ids; strict replays; testnet canary ≥ 14 days with stop/reconcile telemetry; owner approval; rollback = preset revert |

Parallelism:
- PR 1 and PR 2 can run in parallel.
- PR 3 blocks the regime cells.
- PR 4 can be split by flag family (exits / DCA) to keep reviews small.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| **Overfitting / selection bias** (~400 trials; presets already tuned on 2024–26) | Registry + DSR + PBO + Holm; plateau rule; staged funnel; forward holdout; kill rule (no re-tuning on the same data) |
| **Regime label leakage** (centred trend labels, daily close used intraday, EMA warm-up differences) | Decision labels only via availability-time `merge_asof`; `regime_lookahead` on each label; report labels named `ex_post_*` and banned from signal code by a test (grep / import guard) |
| **Fee drag of quick TPs / BE** | Cost floor (§3.3); cost-drag metric; 2× cost stress; maker exit option evaluated separately with fill-telemetry calibration (T05) |
| **Intrabar path bias** (trailing and BE are optimistic on 4h candles) | `pessimistic='worst'` stress; 1h-path re-run for 4h strategies; T05a `hold_eval` upper-bound labelling |
| **Funding model** (constant cost, wrong sign for shorts) | Real funding history (O3); until then, shorts carry a data-quality cap |
| **Short squeezes / gap risk** | Shorts tested with gap-through-stop scenarios; max short notional cap; no short DCA in the first release |
| **Survivorship** (no delisted coins) | Breadth set used only for confirmation; data-quality cap; note in the report |
| **Regime whipsaw** (EMA200 crossings in chop) | Hysteresis (3 closes, 5-day dwell), `btc_bear_conf` slope condition, breadth confirmation |
| **Gate lowers return** (D6 cost ~30% of end equity in 7.1) | Present as an explicit owner trade-off per profile (Calmar vs CAGR). Defaults change only under criterion 1(b) with owner sign-off. |
| **Leverage creep from tighter stops** (D5 1.0-ATR stop) | Report notional/equity and liquidation distance (`lab.liquidation_report`) for every stop-distance variant; never accept a variant that needs a higher `max_lev` |
| **Live/backtest divergence** (KNOWN_DELTAS: candidate_max vs ratchet, dust, BE on bank) | New flags implemented once in `core`; parity tests; new KNOWN_DELTAS entries only with justification |
| **Copy-suitability** (partial TPs and DCA create small orders) | Copy-suitability metric per cell; master profiles evaluated separately |

---

## 10. Open questions for Codex and the owner

1. **O1 — Target profiles:** which profiles should T09a optimise first: `active_dca` (the live DCA1H incident), `steady_mix`, or the future master profile? Criteria 1(b) vs 1(a) depend on this.
2. **O2 — Return vs drawdown trade-off:** D6 (bull-only DCA entries) costs ~30% of end equity for ~40% less DD on DCA1H. Is a Calmar-improving but return-lowering default acceptable to the owner?
3. **O3 — Funding data:** may the harness download Binance historical funding (`/fapi/v1/fundingRate`, public, no keys) into `data_funding/` with a manifest? Is 15m kline data for the S4 scalp worth adding (size ≈ 4× the 1h)?
4. **O4 — Governor Automatic scope:** may it ever *enable* shorts on a `sides='both'` sleeve, or only block and reduce longs? (Decision rule B4 suggests reduce-only. Enabling a short adds new risk, even if the user opted in.)
5. **O5 — Exit policy on open lots when the regime flips** (E6): allowed for Automatic mode, or exit-only by user choice? Today nothing force-closes on regime.
6. **O6 — Per-asset vs BTC regime for alts:** use `coin_daily_trend` for permissions (more trades blocked, less lag) or BTC + breadth only?
7. **O7 — Codex:** should the new flags live in `core.state` (T09-1 transition stages: add a `gate` step before `dca` AddGate) or in `core.levels` only? T09-2 still owns stops/trailing in the engine, so `be_cost`, `trail_from_r`, `giveback` and `stall` may have to wait for T09-2 or land as candidates in `stop_candidates`.
8. **O8 — Codex:** the `tp1_frac=1.0` fix changes backtest output for any user config using it. Classify it as a KNOWN_DELTAS fix (D) or as a bug fix needing a golden re-record?
9. **O9 — Trial-registry location:** `research/` in Git (small JSONL) or the evidence store outside Git like other run artefacts?
10. **O10 — Shadow length:** is 90 days of `side_masked` shadow enough before an E-class short enable, or should it be tied to "≥ 2 bear episodes observed live", which could take far longer?
