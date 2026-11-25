# LONGSCOUT V203: 0/1 frozen screen passes

25 Nov 2026 15:45 Cairo. Source freeze `c256bba731db06d6a5bf54a521f9c7cd5fcc71e2`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 398 completed trials / 13 historical screen passes; 403 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l203_excess_returns_lower_quantile_recovery_1d|142|-0.020008|-0.070709|+0.166985|8.925R Standard|FAIL|

Compares the signed transport of empirical quantiles between consecutive excess-return blocks. V84 compares the variance of positive and negative returns in one window, and V101 classifies the z-score of a peer spread.

l203_excess_returns_lower_quantile_recovery_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134; BTC rising/sideways/falling R15 -0.052174/+0.072507/none; long/short -0.020008/none; fallback-rule path trades (40 repeated orders) 18, order0 0.

l203_excess_returns_lower_quantile_recovery_1d: pooled R23 -0.028896; fresh134 rising/sideways/falling R15 -0.075438/-0.046292/none; fresh134 R23 -0.079272; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
