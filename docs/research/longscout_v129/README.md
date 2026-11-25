# LONGSCOUT V129: 0/1 frozen screen passes

25 Nov 2026 11:42 Cairo. Source freeze `f94b89042e0056f272ec5df358a202537398aaae`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 327 completed trials / 12 historical screen passes; 330 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r129_state_space_transitory_fade_1d|159|+0.017503|+0.044422|-0.063689|7.411R Standard|FAIL|

Separates permanent and transitory movements in one close stream with an online state-space filter. V101 tests mean reversion in a peer-relative spread, V83 fits calendar curvature, and V116 fits fixed-cycle phase. None separately estimates a persistent state and its transitory observation component.

r129_state_space_transitory_fade_1d: failures early, extra31; BTC rising/sideways/falling R15 -0.006591/+0.056876/none; long/short +0.007585/+0.011089; fallback-rule path trades (40 repeated orders) 28, order0 0.

r129_state_space_transitory_fade_1d: pooled R23 +0.009693; fresh134 rising/sideways/falling R15 +0.034006/+0.061489/none; fresh134 R23 +0.036841; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
