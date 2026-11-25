# LONGSCOUT V175: 0/1 frozen screen passes

25 Nov 2026 14:14 Cairo. Source freeze `87e535c2ec0e780ef04ce72545f077256e6bb6c2`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 371 completed trials / 13 historical screen passes; 376 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l175_sofr_upper_tail_relief_1d|3|-0.185938|-0.181862|none|0.640R Standard|FAIL|

Tests normalization of the upper tail of overnight dollar collateral funding while its typical rate holds. V173 compares maturities of Treasury real yields, and V143 tests relative crypto futures funding.

l175_sofr_upper_tail_relief_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.319180/-0.141638/none; long/short -0.185938/none; fallback-rule path trades (40 repeated orders) 2, order0 0.

l175_sofr_upper_tail_relief_1d: pooled R23 -0.193783; fresh134 rising/sideways/falling R15 none/-0.123286/none; fresh134 R23 -0.188798; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: SOFR and its 99th-percentile transaction rate from the public archive, pinned manifest `e6a8bb94a05b950dad569de35e772860533fd37af3e7a3bc11c74c33d733fc36`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
