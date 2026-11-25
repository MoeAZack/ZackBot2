# LONGSCOUT V110: 0/1 frozen screen passes

25 Nov 2026 10:30 Cairo. Source freeze `500bca7e3e52fe9eb1dcda6505eaf019f96371da`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 308 completed trials / 12 historical screen passes; 311 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r110_learned_streak_fade_1d|256|-0.041830|-0.064681|+0.015378|14.157R Standard|FAIL|

Learns next-day reversal after a three-day directional run. V8 and V13 follow persistence after positive streaks. V21 uses unconditional autocorrelation, and V105 learns responses to volume events, not sign runs.

r110_learned_streak_fade_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134; BTC rising/sideways/falling R15 -0.002029/+0.054106/-0.162569; long/short +0.011449/-0.090670; fallback-rule path trades (40 repeated orders) 69, order0 2.

r110_learned_streak_fade_1d: pooled R23 -0.052300; fresh134 rising/sideways/falling R15 -0.051466/+0.022082/-0.153180; fresh134 R23 -0.075035; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
