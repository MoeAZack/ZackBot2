# LONGSCOUT V112: 0/1 frozen screen passes

25 Nov 2026 10:35 Cairo. Source freeze `58c770a17da9ecf3f585dd0a78758f588d1be193`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 310 completed trials / 12 historical screen passes; 313 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l112_turnover_clock_acceleration_4h|307.5|-0.061880|-0.067619|-0.054886|30.443R High|FAIL|

Entry depends on elapsed time at a comparable turnover budget. V99 compares volume shares within calendar days, V85/V95 use calendar-bar volume correlations, and V110 learns reversals after fixed-day sign runs. None bins completed turnover before comparing elapsed durations.

l112_turnover_clock_acceleration_4h: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 +0.011722/-0.054732/-0.253117; long/short -0.061880/none; fallback-rule path trades (40 repeated orders) 66, order0 2.

l112_turnover_clock_acceleration_4h: pooled R23 -0.083201; fresh134 rising/sideways/falling R15 +0.015046/-0.031055/-0.347696; fresh134 R23 -0.088810; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, stress, drawdown, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
