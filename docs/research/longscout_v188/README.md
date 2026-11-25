# LONGSCOUT V188: 0/1 frozen screen passes

25 Nov 2026 14:56 Cairo. Source freeze `d11d12044561925c5216fb52e70a75289b1ec45e`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 384 completed trials / 13 historical screen passes; 389 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l188_cross_coin_directed_leadership_1d|40|+0.012993|+0.081601|-0.167882|3.076R Standard|FAIL|

Selects a directed one-session leader for an individual target. V101 selects a contemporaneous peer for spread reversion, V147 uses spot and perpetual BTC, and V160 estimates a common own-residual response from other coins.

l188_cross_coin_directed_leadership_1d: failures trades, early, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 -0.000915/+0.138167/none; long/short +0.012993/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l188_cross_coin_directed_leadership_1d: pooled R23 +0.002555; fresh134 rising/sideways/falling R15 +0.054010/+0.320720/none; fresh134 R23 +0.070100; fresh134 top-three/net 187.36%; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
