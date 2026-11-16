# Lane 2 funding audit restart — STOPPED / incomplete

Research only. This is a source-gap report, **not completed native-funding economics**.
No strategy has a native-funding result, pass, promotion or activation from this work.

Public probe: 16 November 2026, 16:57–16:58 Africa/Cairo. Five serial unauthenticated
GET requests, at least four seconds apart, all HTTP 200. Raw responses, request URLs,
fetch timestamps, response hashes and returned weight headers are in `public_cache/`.
The initial exploratory funding and mark request preceded this reproducible five-request
probe; seven public calls total were made in this lane. There was no retry storm.

## Finding and stopping reason

The four early 1000LUNCUSDT funding events still have **empty `markPrice`**, although
their containing one-minute mark-price candles can be fetched. These are different
observations. The candles do not identify the exact settlement mark; three returned
funding timestamps are milliseconds after the minute boundary. Even the event exactly
on the boundary lacks an authoritative mapping from candle open to settlement mark.
No candle field was substituted, no event was dropped, and no funding was set to zero.

Completing the requested full-basket, real-funding comparison requires either verified
settlement-mark records or a separately specified, explicitly labelled uncertainty/model
treatment. The latter would be a sensitivity calculation, not verified actual funding.
Entry/exit funding eligibility also needs a declared timing convention: a candle-index
trade timestamp is not an exchange execution timestamp.

There is a second prerequisite: recover/replay position quantities and the already-charged
funding proxy with baseline parity. Published lead trade tapes omit fill quantities,
entry/exit prices and per-bar funding charges. PROT publishes aggregate rows, not position
or fill tapes. These cannot alone support exact cash funding attribution, especially with
partials, adds and compounding. This is not a claim that replay is impossible: it is the
next required implementation and proof, before native funding deltas can be computed.

## Every observed source gap in this probe

All rates below are **0.00010000**. Native settlement mark is missing in all four cases.
Times lead with Africa/Cairo; the raw millisecond event timestamp is retained for audit.

| Cairo event time | UTC event time | event milliseconds | containing-minute O / H / L / C |
|---|---|---|---|
| 8 Jan 2023 02:00:00.015 | 8 Jan 00:00:00.015 | 1673136000015 | .15323921 / .15350000 / .15323477 / .15340000 |
| 8 Jan 2023 10:00:00 | 8 Jan 08:00:00 | 1673164800000 | .15374002 / .15394258 / .15371946 / .15393510 |
| 8 Jan 2023 18:00:00.010 | 8 Jan 16:00:00.010 | 1673193600010 | .15495283 / .15513434 / .15492966 / .15507168 |
| 9 Jan 2023 02:00:00.003 | 9 Jan 00:00:00.003 | 1673222400003 | .15840000 / .15886954 / .15840000 / .15881652 |

Handling of each: retain the rate and OHLC source data, leave settlement mark and cash
funding unavailable, block the complete-basket delta. **This is not an exhaustive gap
inventory across all requested strategies.** Full symbol/event coverage has not been
fetched, certified or inferred from an assumed eight-hour schedule. Other coverage gaps
remain unenumerated; no completeness claim is made for other symbols or later LUNC data.

## Baseline correction: these are not fee/slippage-only results

At pinned LONGSCOUT head `4189908d08c559421bdfd270c02de3f8dd6d97e9`,
`research/extra31_screen/sieve_v1_panel.py` calls `backtest.run` with
`fund_per_bar=0.00005 * bar_h / 4`. The eight requested leads use 4h bars.
`backtest.py` subtracts `qty * candle_close * FPB` from equity and position realised P&L.
The published `pnl15` then adds the 7-to-15bp turnover adjustment. Thus funding proxy
charges are already included; simply adding native funding would double-charge costs.

At pinned PROT head `53753968c52ba2bbe8a6f3c54acb86bdb1bfc8d5`,
`tools/research/prot_compare.py` retains `backtest.FUND_PER_BAR`, which is **0.00005
per 4h bar (0.5bp/4h, nominally 1bp/8h)**. Its report says 3bp/8h. The code/report
disagreement must be reconciled during baseline replay. No result was silently relabelled
as funding-free and no constant proxy was treated as measured native funding.

For a fixed historical trade book, the intended accounting is:

`new net = old net + removed proxy funding + signed native funding cash`.

Each actual event cash flow is `-side * held_quantity * settlement_mark * funding_rate`.
The event-time quantity must include partial closes/adds and an explicit eligibility rule.
Replaying sizing with native equity feedback is a separate experiment and must not be
confused with fixed-book re-costing.

## Per-strategy results — unavailable deltas disclosed

These are the existing **order-0/path closed-trade** totals at 15bp/side and fixed $8
risk, including the existing proxy. They are not the 40-order median screen statistic.

| Lead | closes | original R15 | native delta | re-costed R15 |
|---|---:|---:|---|---|
| r6_regime_flip_4h | 149 | 38.324266 | INCOMPLETE | INCOMPLETE |
| s54_double_top_4h | 111 | 19.177757 | INCOMPLETE | INCOMPLETE |
| l24_breakaway_day_4h | 320 | 11.795537 | INCOMPLETE | INCOMPLETE |
| s26_weekly_close_breakdown_4h | 102 | 17.159174 | INCOMPLETE | INCOMPLETE |
| l40_dual_momentum_4h | 111 | 6.809482 | INCOMPLETE | INCOMPLETE |
| s51_momentum_accel_4h | 223 | 9.150949 | INCOMPLETE | INCOMPLETE |
| s76_uptrend_line_break_4h | 228 | 13.879100 | INCOMPLETE | INCOMPLETE |
| r74_prior_month_break_4h | 218 | 4.839584 | INCOMPLETE | INCOMPLETE |

`per_strategy_status.json` records explicit JSON null deltas for all eight leads and
234 PROT BASE/P1_own baseline rows at 15bp across the published periods/path modes.
The 13 PROT strategies are donchian_ens, ema_mom, rotation, squeeze_tp,
breakout_pyramid, bear_breakdown, ema_st, exp_breakout, exp_mom_short, exp_range,
exp_trend, hot_coin and pullback_rsi. **All 13 remain INCOMPLETE for native funding.**
Other variants and cost levels remain in the byte-preserved input CSVs; they have not been
re-costed either. Open-position costs and unresolved path candles are not zeroed.

`input_manifest.json` pins original paths, source heads and SHA-256 hashes. `inputs/`
contains eight original trade CSVs plus the three PROT result/meta pairs, byte preserved.
Input heads are provenance snapshots, not accepted or merged heads for this lane.

## Reproduce and continue

```
python tools/research/lane2_funding_probe.py
python -m pytest tests/test_lane2_funding_probe.py -q
```

The probe verifies cached response hashes on reuse and makes no requests for valid cached
entries. HTTP/schema/cache failures stop it. It cannot call authenticated/order endpoints.
Eleven targeted tests passed; no full engine suite was run for this research-only tool.

Next work needed: reconstruct baseline-matching fills and proxy-charge tapes in this
isolated clone, freeze timestamp/eligibility and mark-gap treatment, then fetch the full
public event universe with a durable paced cache, enumerate every gap, and report each
strategy/cost/path delta. If exact marks remain unavailable, retain an INCOMPLETE native
result and publish any disclosed OHLC-based bounds separately. No ex-LUNC result replaces
the original full-basket result.

Official endpoint documentation:
[funding history](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History),
[mark-price candles](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price-Kline-Candlestick-Data).

ROADMAP FIT: restart the bounded native-funding audit of the eight named leads and
PROT-CMP-01; this draft supplies source-gap evidence only.
DEFERRED: complete economics pending the prerequisites above; all runtime, account,
settings, scheduler, watchdog, order, canary and mainnet actions; merge and mark-ready.
LANES: Build — other isolated lanes retain their assignments; Evidence — this source-gap
probe finished, complete native-funding comparison blocked; Integration — draft-only
handoff for Claude, no merge or readiness change.
