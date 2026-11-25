# LONGSCOUT V107: 0/1 frozen screen passes

25 Nov 2026 10:18 Cairo. Source freeze `e470a3b1b3bfce3aea4b2c078016f53e649e880f`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 306 completed trials / 12 historical screen passes; 308 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r107_weekly_open_box_fade_1d|238|-0.029089|-0.024630|-0.044257|8.200R Standard|FAIL|

This fades a completed reentry into a weekly-open box toward the open. V12/V74 break prior period extremes, and V70 follows a monthly-open directional cross. Neither fades failed displacement toward a fixed weekly anchor.

r107_weekly_open_box_fade_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.016753/-0.099091/-0.016800; long/short +0.005737/-0.051513; fallback-rule path trades (40 repeated orders) 138, order0 3.

r107_weekly_open_box_fade_1d: pooled R23 -0.040380; fresh134 rising/sideways/falling R15 -0.009636/-0.069076/-0.028862; fresh134 R23 -0.035476; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
