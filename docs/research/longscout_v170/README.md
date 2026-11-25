# LONGSCOUT V170: 0/1 frozen screen passes

25 Nov 2026 14:01 Cairo. Source freeze `abd9c46410fdd9c3b1fbd75c966b910c380c6722`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 366 completed trials / 13 historical screen passes; 371 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l170_alt_perpetual_basis_recovery_breadth_1d|8|-0.010249|-0.061041|none|1.661R Standard|FAIL|

Tests simultaneous normalization of traded bases across alt contracts. V120 measures the BTC premium index, V128 the BTC extreme relative to mark and index, and V143 realized funding ranks.

l170_alt_perpetual_basis_recovery_breadth_1d: failures trades, meanR15, early, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.010249/none/none; long/short -0.010249/none; fallback-rule path trades (40 repeated orders) 5, order0 0.

l170_alt_perpetual_basis_recovery_breadth_1d: pooled R23 -0.018666; fresh134 rising/sideways/falling R15 -0.061041/none/none; fresh134 R23 -0.068961; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: five alt perpetual closes matched to spot index closes from the public archive, pinned manifest `fa6fe984dba1b263c28cd72107958d916246ce54244c62e7029aadd1e5471b10`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
