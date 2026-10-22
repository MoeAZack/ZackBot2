# Strategy research board — 23 October 2026, 02:37 Cairo

This is a point-in-time evidence board, not a permanent verdict. PAPER build `20261023-011441` is on protected master
`9523a65`. The account is PAPER/testnet; ML, Claude analysis and strategy shadow have no order authority.

## Order-taking strategy catalog

The breadth rotation opens at most one six-key group for an eight-hour window. A slot marked enabled below is eligible
only in its own active group and after all shared risk, capacity, cost and data gates pass.

| Book | Strategy key(s) | Current new-entry state |
|---|---|---|
| Fast / scalp | `quick_bank_runner` | Enabled independent 15m PAPER slot; this is the only fast slot still taking new risk |
| Fast / scalp | `exp_scalp`, `range_scalp`, `failed_breakout_scalp`, `momentum_burst_scalp` | Reversibly parked; no new orders, existing protected lots managed, signal shadow continues |
| Breakout / structure | `exp_structure` | Stored enabled but explicitly blocked by the rotation's no-dev-edge gate; shadow only for new signals |
| Range / VWAP | `exp_range`, `exp_vwaprev` | Enabled in their rotation windows |
| Trend / momentum | `donchian_ens`, `ema_mom`, `ema_st`, `exp_mom_short`, `exp_trend`, `hot_coin`, `pullback_rsi`, `rotation` | Enabled in their rotation windows |
| Breakout / structure | `bear_breakdown`, `breakout_pyramid`, `exp_breakout`, `squeeze_tp` | Enabled in their rotation windows |
| Discovery | `dca_dip`, `exp_rip_sell_dca` | Enabled in their rotation windows |

At this snapshot the active breadth group is `ema_st`, `hot_coin`, `exp_vwaprev`, `exp_breakout`, `exp_scalp`, and
`exp_rip_sell_dca`. Because `exp_scalp` is parked, five group members plus independent `quick_bank_runner` can seek new
entries. The group changes at 03:00 Cairo. Disabled strategies are never deleted from research.

## Current forward books

| Book | Closed | Open | Rankable now | Point-in-time result |
|---|---:|---:|---|---|
| B1 Fast / scalp | 73 | 4 | Yes | roughly -7% from its $2,000 start; dominated by failed fast versions and cost drag |
| B2 Range / VWAP | 3 | 1 | Yes, sample tiny | roughly +0.7% |
| B3 Trend / momentum | 2 | 2 | Yes, sample tiny | roughly -3.5% |
| B4 Breakout / structure | 3 | 2 | No | near flat, but two historical fills remain unresolved |
| B5 Discovery | 2 | 0 | Yes, sample tiny | roughly +0.3% |

Only B1 has enough closes to describe, and even B1 is not a durable strategy verdict. B2–B5 are far too small. Book
results share exchange margin and are attribution views, not independent accounts.

## Shadow lanes

- Strategy shadow records all six 15m candidates (`exp_scalp`, `exp_structure`, `failed_breakout_scalp`,
  `momentum_burst_scalp`, `quick_bank_runner`, `range_scalp`) every cycle, including signals skipped because the slot is
  parked, rotated out or the book is full. The current cohort had 9,663 shadow records at this snapshot.
- ML shadow records features and later outcome labels only. It had 213 current-file decisions and 238 labels, plus its
  rotated history. It cannot approve, size, block or place an order. Historical feature missingness must be repaired and
  coverage-proven before model training can influence even a formal candidate score.
- Claude analyst shadow produces periodic read-only audits. Its latest successful report correctly treated the roughly
  five-day forward window as short, identified fast-book losses/cost drag and recommended time-split realistic backtests.
  Its output is untrusted analysis and has no order authority.

## USDT versus USDC research

USDC is not routed into the running PAPER account. The existing Wave-2 comparison is offline research over BTC, ETH,
SOL, XRP, BNB and DOGE twins, across 21 strategy-side cells on 15m and 4h data from 14 October 2024 through
28 September 2026. The holdout was not read.

The packet is `INCOMPLETE`, `non_promotable`, and explicitly says `USDC BETTER: UNAVAILABLE`. The equal-5bp isolation
row shows that quote differences are generally much smaller than strategy differences. Several 4h cells were positive on
both quotes, but the fee-promotion window, standard non-promotional USDC fee schedule, multi-quarter sufficiency and
execution calibration are not frozen strongly enough for a routing decision. No agent may describe USDC as better or
enable it from this packet.

## Next bounded strategy work

1. Finish the exact 1m-under-15m adapter and reconcile why current `quick_bank_runner` PAPER evidence is mildly positive
   while its simplified historical cells are deeply negative. This contradiction is higher priority than inventing a new
   fast strategy.
2. Re-evaluate the strongest existing 4h research cells with the same execution/cost standard: regime-aware
   `pullback_rsi` short, `rotation` long, `exp_trend` long and `ema_mom` long. Their earlier results are hypotheses, not
   promotions, and their activity may be too low for the owner's frequent-trading catalog.
3. Then scout one preregistered frequent hypothesis at a time: regime-gated trend pullback; volatility-compression
   breakout with liquidity/cost admission; cross-sectional relative-strength continuation; and funding/open-interest
   dislocation mean reversion. Long and short remain separate cells.
4. Prefer repairing entry selectivity and cost control before exit optimization. Do not tune an exit to rescue a signal
   that has negative pre-cost expectancy.
5. Only a full development + anchored walk-forward + untouched holdout survivor receives a new version id, shadow cohort
   and bounded PAPER allocation.

ROADMAP FIT: Binance strategy discovery, historical validation, shadow evidence and early trustworthy PAPER.
DEFERRED: USDC routing, unrestricted parameter searches, mainnet and post-Binance venues.
