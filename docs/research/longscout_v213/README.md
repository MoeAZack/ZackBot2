# LONGSCOUT V213: 0/1 frozen screen passes

25 Nov 2026 16:21 Cairo. Source freeze `83d6bb8fc81eacec2b9f8477e86df0b888fa0463`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 406 completed trials / 13 historical screen passes; 412 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r213_mutual_peer_triangle_outlier_fade_1d|141|-0.055106|-0.002846|-0.185622|11.581R Standard|FAIL|

Requires a mutually correlated triangle before fading one coin away from two agreeing peers. V99 selects directed lag leaders independently and V101 fits an OU spread against one highest-correlated peer.

r213_mutual_peer_triangle_outlier_fade_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.017635/-0.119061/none; long/short -0.032848/-0.063858; fallback-rule path trades (40 repeated orders) 56, order0 1.

r213_mutual_peer_triangle_outlier_fade_1d: pooled R23 -0.063389; fresh134 rising/sideways/falling R15 +0.018586/-0.075725/none; fresh134 R23 -0.010806; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
