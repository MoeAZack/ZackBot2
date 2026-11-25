# LONGSCOUT V114: 0/1 frozen screen passes

25 Nov 2026 10:40 Cairo. Source freeze `7c4d10187de55f888511f1616f9a0de6922cfc93`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 312 completed trials / 12 historical screen passes; 315 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l114_positive_btc_convexity_1d|22|+0.253312|+0.259907|+0.214970|2.406R Standard|FAIL|

Tests response to the magnitude of BTC movement after removing linear beta. V33 and V93 use linear beta or downside-only linear beta. V97 uses linear BTC residuals while BTC is quiet, and V83 fits curvature over calendar time. None uses squared BTC shocks as an independent timing factor in both directions.

l114_positive_btc_convexity_1d: failures trades, order0_30_per_half; BTC rising/sideways/falling R15 +0.313798/+0.105616/none; long/short +0.253312/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l114_positive_btc_convexity_1d: pooled R23 +0.245239; fresh134 rising/sideways/falling R15 +0.143081/+0.335329/none; fresh134 R23 +0.251625; fresh134 top-three/net 155.84%; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
