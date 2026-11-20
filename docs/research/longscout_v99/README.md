# LONGSCOUT V99: 0/2 frozen screen passes

20 Nov 2026 17:10 Cairo. Source freeze `230bb04b958b49ef60bcc24f5b9a284a68072cf9`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 297 completed trials / 11 historical screen passes; 297 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l99_peer_leader_catchup_4h|57|+0.055588|+0.011843|+0.196278|11.530R Standard|FAIL|
|r99_early_volume_stress_recovery_4h|110|+0.022180|+0.026448|+0.000651|7.604R Standard|FAIL|

l99_peer_leader_catchup_4h: failures trades, ex_best_coin, order0_30_per_half; BTC rising/sideways/falling R15 -0.016887/-0.099800/+0.647211; long/short +0.055588/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

r99_early_volume_stress_recovery_4h: failures late, ex_best_coin; BTC rising/sideways/falling R15 +0.021155/+0.006730/+0.060184; long/short +0.022180/none; fallback-rule path trades (40 repeated orders) 42, order0 1.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
