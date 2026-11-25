# LONGSCOUT V177: 0/1 frozen screen passes

25 Nov 2026 14:20 Cairo. Source freeze `36ad7b11cd2829b2a08b8c7acf8252c68a112b31`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 373 completed trials / 13 historical screen passes; 378 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l177_ecb_cyclicals_vs_havens_recovery_1d|19|-0.153958|-0.179881|none|5.700R Standard|FAIL|

Tests rotation between two cyclically sensitive and two haven currencies using a common quote that cancels. V132 tests crypto spot triangular pricing, V121 stablecoin depegging, and V173 US real interest rates.

l177_ecb_cyclicals_vs_havens_recovery_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.206808/-0.043996/none; long/short -0.153958/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l177_ecb_cyclicals_vs_havens_recovery_1d: pooled R23 -0.163068; fresh134 rising/sideways/falling R15 -0.208857/-0.126038/none; fresh134 R23 -0.188247; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: ECB AUD, CAD, CHF and JPY reference exchange rates from the public archive, pinned manifest `515ed02a116ff56a76883b6920bde56960da89bd509ac606097ec010e086b208`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
