# LONGSCOUT V207: 0/1 frozen screen passes

25 Nov 2026 16:00 Cairo. Source freeze `aa304a7537215ec28180da8f6a2aa35cab630031`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 402 completed trials / 13 historical screen passes; 407 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r207_causal_forecast_interval_reentry_1d|140|+0.007049|+0.024802|-0.052472|6.771R Standard|FAIL|

Calibrates entry with the realized errors of past one-step forecasts and uses causal reentry into asymmetric empirical bounds. V3 uses Bollinger bands for rolling price dispersion and V129 uses a latent-state innovation model.

r207_causal_forecast_interval_reentry_1d: failures early, ex_best_coin, meanR23, extra31; BTC rising/sideways/falling R15 -0.015515/+0.057506/none; long/short +0.047569/-0.022324; fallback-rule path trades (40 repeated orders) 46, order0 1.

r207_causal_forecast_interval_reentry_1d: pooled R23 -0.001258; fresh134 rising/sideways/falling R15 +0.002080/+0.075963/none; fresh134 R23 +0.016767; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
