# LONGSCOUT V191: 0/1 frozen screen passes

25 Nov 2026 15:06 Cairo. Source freeze `e853fb3a0c7c75c7c2721da2342e221a8dd83a06`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 387 completed trials / 13 historical screen passes; 392 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l191_treasury_delivery_fails_normalize_1d|3|-0.238477|none|none|0.785R Standard|FAIL|

Uses failed delivery in the Treasury settlement process. V175 uses the cost of financing collateral, V182 uses Fed balances, and V173 uses real yields.

l191_treasury_delivery_fails_normalize_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.238477/none/none; long/short -0.238477/none; fallback-rule path trades (40 repeated orders) 3, order0 0.

l191_treasury_delivery_fails_normalize_1d: pooled R23 -0.244454; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: NY Fed cumulative primary-dealer Treasury fails-to-deliver from the public archive, pinned manifest `47a95ed6fbca30a8f9f048bddd226df7db15f89f344d984d5ff7296c8a8ed512`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
