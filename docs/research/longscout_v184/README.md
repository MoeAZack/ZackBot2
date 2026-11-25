# LONGSCOUT V184: 0/1 frozen screen passes

25 Nov 2026 14:43 Cairo. Source freeze `6efa944cf88d7cb8caab5405383b6cef8a45461c`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 380 completed trials / 13 historical screen passes; 385 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r184_overlap_of_three_completed_weeks_1d|27|+0.061532|+0.034104|+0.142002|1.470R Standard|FAIL|

Freezes the intersection of three nonoverlapping weekly ranges with elapsed-time expiry. V45 uses the most extreme Donchian closes, V100 compares close distributions, and V102 estimates volume-weighted auction acceptance.

r184_overlap_of_three_completed_weeks_1d: failures trades, early, order0_30_per_half; BTC rising/sideways/falling R15 +0.088990/-0.158132/none; long/short +0.052227/+0.094097; fallback-rule path trades (40 repeated orders) 0, order0 0.

r184_overlap_of_three_completed_weeks_1d: pooled R23 +0.053235; fresh134 rising/sideways/falling R15 +0.044687/-0.066435/none; fresh134 R23 +0.025786; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
