# LONGSCOUT V176: 0/1 frozen screen passes

25 Nov 2026 14:18 Cairo. Source freeze `96d21f042b4d4d308b10f079c3cc030bb256fe38`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 372 completed trials / 13 historical screen passes; 377 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l176_cme_positioning_long_after_short_covering_1d|6|+0.022332|none|none|1.094R Standard|FAIL|

Uses gross and net positions in CFTC asset-manager and leveraged-fund categories for listed CME Bitcoin futures. V119 compares native Binance trader concentration with account counts and V142 compares Binance account cohorts.

l176_cme_positioning_long_after_short_covering_1d: failures trades, late, ex_best_coin, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 +0.022332/none/none; long/short +0.022332/none; fallback-rule path trades (40 repeated orders) 5, order0 0.

l176_cme_positioning_long_after_short_covering_1d: pooled R23 +0.014497; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: CFTC CME Bitcoin futures-only position categories from the public archive, pinned manifest `ac9df9124ce3907a56b402af4002b402bdb83ee2fa677840d2bcd32052a57f75`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
