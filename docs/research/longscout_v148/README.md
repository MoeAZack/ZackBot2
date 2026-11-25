# LONGSCOUT V148: 0/1 frozen screen passes

25 Nov 2026 12:48 Cairo. Source freeze `ffebfbc0343342ba52f25ff65f70960f58c195df`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 346 completed trials / 13 historical screen passes; 349 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r148_discrete_range_regime_posterior_1d|216|-0.006750|+0.025362|-0.111800|6.415R Standard|FAIL|

Uses a recursively filtered probability of a discrete low-volatility regime. V45 uses observable sideways R1; V126 estimates barrier-hit odds; V129 decomposes price into continuous trend and transitory levels, without a discrete regime posterior.

r148_discrete_range_regime_posterior_1d: failures meanR15, early, late, ex_best_coin, meanR23, extra31; BTC rising/sideways/falling R15 +0.004783/-0.031035/none; long/short -0.027436/+0.005698; fallback-rule path trades (40 repeated orders) 112, order0 2.

r148_discrete_range_regime_posterior_1d: pooled R23 -0.015043; fresh134 rising/sideways/falling R15 +0.028028/+0.016715/none; fresh134 R23 +0.017028; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
