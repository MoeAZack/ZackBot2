# LONGSCOUT V208: 0/1 frozen screen passes

25 Nov 2026 16:05 Cairo. Source freeze `17a778b6bb0ddbb9556d8344367dfec1137042fa`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 403 completed trials / 13 historical screen passes; 408 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l208_commercial_crude_rebuild_steady_utilization_1d|7|+0.082144|+0.111777|none|1.704R Standard|FAIL|

Tests the physical US crude inventory balance alongside refinery use. V175 measures funding in overnight repo and V205 measures US bill auction allocations. Neither uses oil supply or processing quantities.

l208_commercial_crude_rebuild_steady_utilization_1d: failures trades, early, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.081586/+0.020363/none; long/short +0.082144/none; fallback-rule path trades (40 repeated orders) 3, order0 0.

l208_commercial_crude_rebuild_steady_utilization_1d: pooled R23 +0.074472; fresh134 rising/sideways/falling R15 +0.122114/none/none; fresh134 R23 +0.103049; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: EIA commercial crude stocks, refinery use and release dates from the public archive, pinned manifest `4b8876b2c9675e4ad64f8349cc27d1db9003955ad667a9ce6f1a069806a4b490`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
