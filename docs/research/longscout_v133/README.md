# LONGSCOUT V133: 0/1 frozen screen passes

25 Nov 2026 11:55 Cairo. Source freeze `26fe46d69a921a439cb62bb1ebd2bb80888b3ed8`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 331 completed trials / 12 historical screen passes; 334 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r133_ordered_historical_analogue_fade_1d|142|-0.072625|-0.058483|-0.131429|15.336R Elevated|FAIL|

Matches chronological return paths to fully matured historical neighbours. V105/V110 condition on single event categories and V100 compares unordered return distributions. None learns from ordered nearest paths.

r133_ordered_historical_analogue_fade_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.059892/-0.126430/none; long/short -0.080983/-0.068992; fallback-rule path trades (40 repeated orders) 67, order0 1.

r133_ordered_historical_analogue_fade_1d: pooled R23 -0.081721; fresh134 rising/sideways/falling R15 -0.073913/-0.070228/none; fresh134 R23 -0.066734; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
