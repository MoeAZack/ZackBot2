# R6 regime flip — independent signal and concentration review

ROADMAP FIT: evidence for the first LONGSCOUT screen survivor in the native-Binance strategy conveyor. DEFERRED: activation, account changes, tuning and new gates. This is a consumed-calendar, selected-universe screen lead, not proof of durable profit.

PR #347's observed current head was `776d3ecefff5a69e7ea3931b95e7f45966100bbb`; the reviewed result head `10486f35dd513c51b375b3b72cfe5864763a7b39` is its ancestor and its V6 source, cases and result paths are unchanged. Strategy source `acc8d0eea5fbc964d45d582923732e2bbb5ba322`; frozen cases `eb5f615c78660490e76cad97b8f09c2fffbe1e89`. The PR can advance after this observation.

## Independent signal and timing checks

All 166 source CSVs (fresh BTC context, 134 fresh and 31 extra traded coins) matched their pinned manifests. Independent code, without the submitted strategy or helpers, rebuilt daily regime, first flips and D01 eligibility: **6,075/6,075 signal tuples match**, including **3,722/3,722 eligible**, with zero missing, extra or duplicate tuples. All 149 order-zero trade entries have a same-symbol, same-side eligible signal on the preceding 4h bar.

The signal's UTC 20:00 bar ends at the completed daily close, after which the native next-bar fill occurs; all 11,983 observed valid regime state changes occur on that bar. All input 4h series were contiguous. Thirty-six prefix truncations across 12 coins at three points produced zero differences in earlier independently rebuilt long/short signals. Together with the scoped source inspection, this supports the completed-day availability and causal entry clock; it is not a formal proof of all possible inputs or an independent execution simulation. Current 4h data, not incomplete daily candles, supplies each daily close. The sample cutoff test exercises the independent implementation, while full-window tuple matching compares it with the published output.

## Concentration and interpretation

Independent arithmetic over the submitted 40 path-order trade sets reproduces median mean R15 **+0.331701**, ex-best-coin **+0.176817**, ex-top-three **+0.007832**, ex-top-five **-0.089417** and ex-top-ten **-0.254929**. Minimum order mean is **+0.020464**. These exclusions are post-hoc diagnostics and do not add or alter a frozen gate. The strong concentration and 38 attempted candidates limit confidence; pooled order trades repeat the same market episodes and are not independent observations.

On order zero, 29 of 89 traded coins are positive and P&L at 15 bp divided by $8 sums to **+38.324R15**: +26.710 long and +11.614 short. The source README's **+40.9R** total is instead native/base-cost `R`; it mixes that total with the R15 side sums. Similarly its pooled median **-0.393R** and maximum **+32.048R** refer to native/base-cost `R`; pooled 15 bp P&L/$8 has median **-0.420R** and maximum **+31.998R**. Frozen gate calculations use R15 and are unaffected by these reporting ambiguities.

This review did not independently simulate the full cash/admission path or decide PAPER fit; integration's separate full simulation is required. `independent_review.py`, `independent_signals.csv` and `independent_review.json` contain the reproducible evidence and complete input hashes. No accounts, keys, runtime, schedules or trades were touched.
