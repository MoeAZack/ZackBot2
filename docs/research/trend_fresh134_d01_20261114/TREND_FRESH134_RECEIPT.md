# TREND-FRESH134-D01 — economic receipt

ROADMAP FIT: strategy economics and account-4 replacement research. DEFERRED: activation, engine changes, UI, other venues and mainnet. **CONSUMED_CALENDAR_SYMBOL_TRANSFER_DIAGNOSTIC:** existing `exp_trend`, not a new independent strategy or untouched validation. Neither run qualifies a wider account universe.

## Decision

Both frozen 40-order runs **FAIL**. Max1 fails the positive earlier-half, ex-best-coin and 23 bp gates. Max3 fails the 23 bp gate; it cannot override the max1 failure. Estimates reflect modelled costs and a funding proxy, not proof of future profitability.

| Frozen case | Median closes | Mean R7 | Mean R15 | Mean R23 | Early R15 | Late R15 | Ex-best R15 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Max1 primary | 466 | +0.034018 | +0.009408 | -0.015215 | -0.012147 | +0.045776 | -0.005759 |
| Max3 secondary | 1,154 | +0.043326 | +0.018854 | -0.005617 | +0.016262 | +0.023395 | +0.007507 |

Rule-v1 gates use medians across 40 fixed symbol orders, with order0 at least 30 completed closes in each half. Both path and worst-path diagnostics give the same medians here. Raw signals 7,043; eligible 3,815. Max1 order0: 472 closes (290 early/182 late), 2.20/week, 114 traded coins, 41 positive; closed-trade DD 22.45 R15. Max3 order0: 1,166 closes (719/447), 5.44/week, 128 traded, 55 positive; closed-trade DD 33.51 R15. DD is closed-trade based, not marked-account DD. Full per-coin, both-path, feasibility, and order detail remains in result JSON and order0 trade CSVs.

## Costs and terminal state

Order0 completed trades, USD, fixed-$8 initial-risk target subject to native sizing: max1 gross reference +196.06, slippage -23.17, fees -57.91, implied funding proxy -36.08, base 7 bp P&L +78.90; extra 8 bp/side -92.66 gives 15 bp P&L -13.76, extra 16 bp/side -185.33 gives 23 bp P&L -106.42. Max3: gross +705.20, slippage -57.24, fees -143.10, implied funding -90.16, base +414.69; additional -228.97 gives +185.72 at 15 bp; additional -457.94 gives -43.25 at 23 bp. Funding is reconstructed as the residual of engine P&L after fill-level gross, slippage and fees, and is a uniform unsigned model charge, not historical signed funding. Repricing follows the frozen driver after base-cost admission.

Independent observer reran the unchanged engine on order0 for each case and exactly matched every exported trade row. Both ended flat: no unclosed fills at final 4h bar 03:00 Cairo on 4 October 2026 (00:00 UTC). Native base-cost marked equity was $10,000,078.90 max1 and $10,000,414.69 max3; it is not the 15 bp repriced equity and is separate from the completed-trade P&L above.

Native simulation uses $10m equity to approximate fixed $8 risk and $3,333.33 notional cap; realized equity drift means the risk/cap are not exact fixed values. Current testnet exchange rules are a historical proxy, with native legacy fallback for unknown `ICPUSDT` and `TLMUSDT`. D01 historical liquidity eligibility and 220 own 4h bars apply; execution, slippage, filters, funding and fill paths are research approximations. This is a consumed calendar and a selected symbol set, not new market-time evidence.

## Inputs, frozen proof and output interpretation

Contract commit `11fef8dab49fa318b89c1ec640c22aa3fdff8837`; original driver `45fe30bcc8ea7b4ae1e5d3d66f7f3d1d73523b3f`; native strategy/engine `9cb4341f2d94c21434492515b8e85f2b42c148ce`. Local private evidence repo committed the complete source, driver, wrapper, contract, selected coins and all 135 data hashes **before outcomes** at `16e9053420ab6929ce3eafbe5a16a0654001bfd3`; `frozen_hashes.json` SHA256 `d8add0590ca014b454cae9149e960cf03f6af0725f494b7878be969f8d25529f`. Input manifest SHA256 `fb0c0572b6c2bc5f569c64d89bd0ee2ca1dd9e3cf96b1ed8bd1110c341ed2f3e`; every BTC+134 CSV hash matched before any test and again on each frozen launcher invocation. Wrapper SHA256 `45e590f4b6723317d42a93627392022289169cc463b16f6c7830acf2c12fc386`; unmodified panel worker SHA256 `6da5a8493a66015ebe2f16f1bb3dff62231b56b37b3b8a3f8b476e6baf5de075`; native strategies `0d416c8ca93788827aabe8374dae9f397f23c68213727d8725dc574071b8b011`, backtest `fdab284ce3ebe0c8329be9f3e8eebce6fde23bdbe44a35d0d785722e0b54d4ae`. Remaining source and input hashes are in the frozen manifest.

The unchanged driver's raw metadata says `eligible_coins: extra31` inside `frozen_case` and `universe: smoke` when any CLI universe override is supplied. These are inherited driver annotations, **not** the executed universe: the frozen wrapper passed and validated the 134 selected symbols (BTC context only), and each result's `coins`/`n_coins` records that actual list. Outputs were not rewritten.

`primary40/exp_trend_result.json`, `secondary40/exp_trend_mp3_result.json`, their `*_signals.csv` and `*_order0_*_trades.csv`, `economics_summary.json`, and `diagnostic/` hold the result, fills and replay evidence. `order0/` is the preliminary single-order run. The post-result observer only captures unchanged engine output; it is not an independent alpha review. No accounts, keys, runtime or schedulers were touched.

## Published byte preservation

Readable text copies use LF. Original pre-outcome frozen bytes (including CRLF where present) are preserved exactly in raw_freeze_inputs.zip; use its files for the receipt's byte hashes. The native source/driver exports are pinned by commit and frozen hashes. SHA256.json hashes the published artifacts, not the original source-side files.
