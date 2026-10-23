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

| Book | Closed (n) | Open | Measured / booked closes | Build / cohort | Rankable now | Point-in-time result |
|---|---:|---:|---|---|---|---|
| B1 Fast / scalp | 73 | 4 | [DATA: measured n / booked n] | [DATA: per build/cohort split] | Describable only, not a verdict | roughly -7% from its $2,000 start (n=73); dominated by failed fast versions and cost drag |
| B2 Range / VWAP | 3 | 1 | [DATA] | [DATA] | **No** (n=3) | roughly +0.7% (n=3) |
| B3 Trend / momentum | 2 | 2 | [DATA] | [DATA] | **No** (n=2) | roughly -3.5% (n=2) |
| B4 Breakout / structure | 3 | 2 | [DATA]; two historical fills unresolved | [DATA] | **No** (n=3, incomplete) | near flat (n=3) |
| B5 Discovery | 2 | 0 | [DATA] | [DATA] | **No** (n=2) | roughly +0.3% (n=2) |

Only B1 has enough closes to describe, and even B1 is not a durable strategy verdict. B2, B3 and B5 are not rankable
at these sample sizes, and B4 is not rankable while fills are unresolved. Book results share exchange margin and are
attribution views, not independent accounts. Forward rows are attributed to the PAPER build / shadow cohort that
produced them: rows from superseded builds or retired strategy versions are reported separately and never pooled with
the current build's rows.

### `quick_bank_runner` (measured-net closed rows)

| Window (Cairo) | Closed n (measured net) | Mean net R | Uncertainty | Open lots |
|---|---:|---:|---|---:|
| [DATA: first close .. snapshot] | [DATA] | [DATA] | [DATA: e.g. 95 % interval / t] | [DATA] |

Only closed rows with a measured net (exchange fill price and fee) are counted; booked-only rows are listed, not
pooled. **Limitation:** open lots that have taken TP1 show only their realised first leg; their remainder is unrealised,
so a TP1-heavy open book can look better than its eventual closed result. The figure above excludes open lots.

## Shadow lanes

- Strategy shadow records all six 15m candidates (`exp_scalp`, `exp_structure`, `failed_breakout_scalp`,
  `momentum_burst_scalp`, `quick_bank_runner`, `range_scalp`) every cycle, including signals skipped because the slot is
  parked, rotated out or the book is full. Coverage at this snapshot: [DATA: expected cycles x candidates] expected,
  [DATA] observed ([DATA] %); a raw record count (9,663) is not a coverage measure. Its fixed-cost model shows that
  signals are captured; it does not show that any of them is profitable.
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
both quotes; with 21 strategy-side cells compared on two quotes, these are multiplicity-adjusted hypotheses from an
`INCOMPLETE` packet, not findings. The fee-promotion window, standard non-promotional USDC fee schedule, multi-quarter sufficiency and
execution calibration are not frozen strongly enough for a routing decision. No agent may describe USDC as better or
enable it from this packet.

## Next bounded strategy work

1. Finish the exact 1m-under-15m adapter and reconcile why current `quick_bank_runner` PAPER evidence is mildly positive
   while its simplified historical cells are deeply negative. This contradiction is higher priority than inventing a new
   fast strategy.
2. Re-evaluate the strongest existing 4h research cells with the same execution/cost standard, labelled as
   hypotheses: **H1** regime-aware `pullback_rsi` short, **H2** `rotation` long, **H3** `exp_trend` long, **H4**
   `ema_mom` long. They were selected after an unquantified number of earlier tests (committed research tables alone hold at
   least 10 / 12 / 0 / 10 rows naming H1 / H2 / H3 / H4, a lower bound, per Cowork 6357524514; the earlier 4h screens,
   the 21 USDC cells and runs outside the repo are not in them), so their earlier results are hypotheses, not
   promotions. A full count must be frozen before any deflated statistic is quoted. Each must beat a buy-and-hold baseline on the
   same symbols and window, after costs, before it counts. Their trade frequency is low, which conflicts with the
   owner's frequent-trading catalog: a survivor joins it only as a low-frequency book, labelled as such.
3. Then scout one preregistered frequent hypothesis at a time: regime-gated trend pullback; volatility-compression
   breakout with liquidity/cost admission; cross-sectional relative-strength continuation; and funding/open-interest
   dislocation mean reversion. Long and short remain separate cells.
4. Prefer repairing entry selectivity and cost control before exit optimization. Do not tune an exit to rescue a signal
   that has negative pre-cost expectancy.
5. Only a full development + anchored walk-forward + untouched holdout survivor receives a new version id, shadow cohort
   and bounded PAPER allocation.

ROADMAP FIT: Binance strategy discovery, historical validation, shadow evidence and early trustworthy PAPER.
DEFERRED: USDC routing, unrestricted parameter searches, mainnet and post-Binance venues.
