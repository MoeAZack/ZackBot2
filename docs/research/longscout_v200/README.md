# LONGSCOUT V200: 0/1 frozen screen passes

25 Nov 2026 15:36 Cairo. Source freeze `1fc49851ada9cda8f9d05242e07176d20cda84d3`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 395 completed trials / 13 historical screen passes; 400 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l200_coin_btc_rebalancing_premium_fade_1d|155|-0.020411|-0.074084|+0.167760|9.449R Standard|FAIL|

Compares daily-rebalanced and untouched BTC/coin pair wealth. V81 and V91 measure own serial dependence, V95 ranks recent return and V101 fits a peer spread; none measures the relative return of these two portfolios.

l200_coin_btc_rebalancing_premium_fade_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134; BTC rising/sideways/falling R15 -0.058081/+0.059866/none; long/short -0.020411/none; fallback-rule path trades (40 repeated orders) 24, order0 0.

l200_coin_btc_rebalancing_premium_fade_1d: pooled R23 -0.028716; fresh134 rising/sideways/falling R15 -0.099114/+0.006403/none; fresh134 R23 -0.081917; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
