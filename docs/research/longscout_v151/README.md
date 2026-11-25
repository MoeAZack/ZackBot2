# LONGSCOUT V151: 0/1 frozen screen passes

25 Nov 2026 12:56 Cairo. Source freeze `1c79748a94dae8be53f4e63e88377233adc9de03`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 349 completed trials / 13 historical screen passes; 352 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r151_btc_trade_arrival_concentration_1d|2|+0.250087|none|none|0.025R Standard|FAIL|

Tests concentration in the timing of trade arrivals after controlling for UTC slot and aggregate dollar turnover. V89 studies dollar-volume allocation across days, V112 uses turnover as a clock, and V98 addresses taker direction or average print size without modeling the timing of arrival clusters.

r151_btc_trade_arrival_concentration_1d: failures trades, ex_best_coin, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/+0.250087/none; long/short +0.298374/none; fallback-rule path trades (40 repeated orders) 2, order0 0.

r151_btc_trade_arrival_concentration_1d: pooled R23 +0.242245; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC USD-M counts and quote turnover in six 4h slots from the public archive, pinned manifest `7a67fc9a48039bb9815a97d0c0da1042f153fab44ca46bbc5d7c6473c7828a5e`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
