# LONGSCOUT V204: 0/1 frozen screen passes

25 Nov 2026 15:49 Cairo. Source freeze `dc2b014576750c0fd3163d708d3003fe897650c6`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 399 completed trials / 13 historical screen passes; 404 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r204_nyse_full_closure_us_open_fade_4h|13|-0.298143|-0.322942|-0.155186|5.422R Standard|FAIL|

Uses published full US cash-equity closures as a necessary exogenous condition for fading the usual open window. V93 studies intraday displacements and V106 learns weekday returns without venue closures.

r204_nyse_full_closure_us_open_fade_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.386999/+0.064335/none; long/short -0.444903/+0.088494; fallback-rule path trades (40 repeated orders) 2, order0 0.

r204_nyse_full_closure_us_open_fade_4h: pooled R23 -0.320123; fresh134 rising/sideways/falling R15 -0.448580/+0.213612/none; fresh134 R23 -0.344170; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
