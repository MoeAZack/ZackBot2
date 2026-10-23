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

Snapshot 23 October 2026, 07:44 Cairo (Codex 6357656908). All 103 closed rows carry cohort
`CH-20261020T100344Z-6b4aba0e`. Closed rows do not store an exact build ID, so no row is attributed to a build; rows
from different evidence classes are never pooled.

| Book | Closed (n) | Open | Directly measured / acknowledged / legacy / pending | Net by evidence class | Rankable now |
|---|---:|---:|---|---|---|
| B1_FAST | 92 | 3 | 48 / 4 / 40 / 0 | measured −$363.1791; acknowledged −$65.9351; legacy +$78.4979 | Describable only, not a verdict |
| B2_RANGE | 4 | 0 | 1 / 0 / 3 / 0 | measured +$4.5261; legacy +$0.5465 | **No** (n=4) |
| B3_TREND | 2 | 3 | 0 / 2 / 0 / 0 | acknowledged −$39.1406 | **No** (n=2) |
| B4_BREAKOUT | 3 | 2 | 0 / 1 / 0 / 2 | acknowledged −$30.6722; two pending/unusable | **No** (n=3, two pending) |
| B5_DISCOVERY | 2 | 0 | 0 / 1 / 1 / 0 | acknowledged +$4.1961; legacy +$1.5180 | **No** (n=2) |

Only B1 has enough closes to describe, and even B1 is not a durable strategy verdict. B2, B3 and B5 are not rankable
at these sample sizes, and B4 is not rankable while fills are pending. Book results share exchange margin and are
attribution views, not independent accounts. Build attribution needs a build ID on each row; until rows carry one, the
cohort is the only attribution and temporal context is labelled as such.

### `quick_bank_runner` (fully closed, directly measured rows only)

| Window (Cairo) | Closed rows | Directly measured (n) | Measured net | Mean net R | Provisional 95 % interval | Open lots |
|---|---:|---:|---:|---:|---|---:|
| 22 Oct 16:45:50 – 23 Oct 07:32:37 | 27 (5 legacy booked, 0 acknowledged/pending) | 22 | −$238.1557 (−13.3493R) | −0.6068R | [−0.9408R, −0.2728R] (Student t, sample SD 0.7533R) | 3 (AIN, QNT, ZEC short) |

Measured net = `pnl_gross + pnl_fill_adj − fees_actual + funding_actual`; net R = measured net / `risk_usd`; the
evidence class follows `actual_costs.row_view()`. The interval is descriptive, not a durable-edge verdict: clustering
and symbol reuse violate independence and may make it optimistic. The legacy rows are not pooled. The current live
session/build `20261023-011441` began 23 Oct 02:20:44 Cairo; 16 of the 22 measured rows opened and closed after that
start, but rows lack build IDs, so this is temporal context only, not build attribution. **Limitation:** open lots
that have taken TP1 show only their realised first leg; their remainder is unrealised, so a TP1-heavy open book can
look better than its eventual closed result. The figures above exclude the three open lots.

## Shadow lanes

- Strategy shadow records all six 15m candidates (`exp_scalp`, `exp_structure`, `failed_breakout_scalp`,
  `momentum_burst_scalp`, `quick_bank_runner`, `range_scalp`) every cycle, including signals skipped because the slot is
  parked, rotated out or the book is full. Coverage for cohort CH-20261020T100344Z-6b4aba0e from 20 Oct 13:15
  through 23 Oct 02:30 Cairo: 1,476 candidate-cycle observations expected (246 scheduled 15m cycles × six candidates),
  1,332 observed across 222 unique complete cycles (90.2%). Twenty-four scheduled cycles are absent; five duplicate
  cycle records are replay duplicates and are not counted. The 9,663 raw ledger records are not a coverage
  denominator. Its fixed-cost model shows that signals are captured; it does not show that any of them is profitable.
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

1. Finish the exact 1m-under-15m adapter and reconcile `quick_bank_runner`'s PAPER evidence, which is negative in
   this small measured snapshot (n=22, mean −0.6068R), with its simplified historical cells. The exact-rule historical
   contradiction remains open. This is higher priority than inventing a new fast strategy.
2. Re-evaluate the strongest existing 4h research cells with the same execution/cost standard, labelled as
   hypotheses: **H1** regime-aware `pullback_rsi` short, **H2** `rotation` long, **H3** `exp_trend` long, **H4**
   `ema_mom` long. These four cells are hypotheses nominated from heterogeneous legacy screens, not promotions. The committed
   append-only breadth ledger records one preregistration for each H1–H4 but does not contain a complete, deduplicated
   inventory of completed prior trials. As documentary context only, legacy committed CSV tables contain 10 rows naming
   pullback_rsi, 12 naming rotation and 10 naming ema_mom; no row naming exp_trend was found. Those row counts mix
   sides/timeframes and are neither exact trial counts nor valid multiplicity denominators. Freeze a complete trial
   inventory before quoting any deflated or multiplicity-adjusted statistic. Each must beat a buy-and-hold baseline on the
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
