# LONGSCOUT V120: 0/1 frozen screen passes

25 Nov 2026 11:06 Cairo. Source freeze `4d2eaaecdaec65a69fe88caa7ccc2c4efe907b47`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 318 completed trials / 12 historical screen passes; 321 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r120_btc_premium_convergence_fade_4h|5|+0.025618|none|none|1.417R Standard|FAIL|

Uses short-horizon convergence of the BTC premium index against concurrent price direction. V117 uses settled funding sign under a price breakout; V118 and V119 use open interest or position concentration. Neither measures premia reverting while the futures price continues the other way.

r120_btc_premium_convergence_fade_4h: failures trades, late, ex_best_coin, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.231972/+0.790693/none; long/short +0.259625/-0.111390; fallback-rule path trades (40 repeated orders) 2, order0 0.

r120_btc_premium_convergence_fade_4h: pooled R23 +0.007999; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC 4h premium-index closes from the public archive, pinned manifest `c28f63707a0531074df43357e3a74a16a0f8b068275f73bac31cf2a69df7b300`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
