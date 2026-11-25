# LONGSCOUT V108: 0/1 frozen screen passes

25 Nov 2026 10:22 Cairo. Source freeze `2199e2c2d6aec6e0a555da8f3e80db0e5af00407`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 307 completed trials / 12 historical screen passes; 309 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l108_peer_rank_rotation_1d|106|-0.017187|-0.056728|+0.032632|12.404R Standard|FAIL|

This rotates on entry to and exit from observable cross-sectional rank bands. V24 ranks against the coin’s own history, V40 compares only with BTC, V94 ranks attention for entry, and V99 uses lagged peer leadership to predict catch-up.

l108_peer_rank_rotation_1d: failures meanR15, early, ex_best_coin, meanR23, fresh134; BTC rising/sideways/falling R15 -0.079414/+0.139490/+0.129098; long/short -0.017187/none; fallback-rule path trades (40 repeated orders) 31, order0 1.

l108_peer_rank_rotation_1d: pooled R23 -0.024940; fresh134 rising/sideways/falling R15 -0.061637/+0.011362/-0.039316; fresh134 R23 -0.064379; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
