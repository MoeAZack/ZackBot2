# LONGSCOUT V123: 0/1 frozen screen passes

25 Nov 2026 11:17 Cairo. Source freeze `a5228ce45dd87e6c255f2b55d7b92d65899f3e7b`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 321 completed trials / 12 historical screen passes; 324 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r123_funding_cadence_change_fade_4h|1|-0.563406|-0.563406|none|0.563R Standard|FAIL|

Tests the first verified change to a faster funding schedule as a market mechanics event. V117 tests the sign of settled funding payments and V120 tests premium convergence. This signal does not use funding amounts or premia.

r123_funding_cadence_change_fade_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/-0.563406/none; long/short -0.563406/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

r123_funding_cadence_change_fade_4h: pooled R23 -0.567823; fresh134 rising/sideways/falling R15 none/-0.563406/none; fresh134 R23 -0.567823; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: public Funding Rate archive, pinned manifest `324c4ebf7f05798f74e74ed9772310ea44c0216efe1db8b4d6786950a619e691`. The 166-symbol release masks missing-cadence windows. Simulator funding costs remain fixed.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
