# LONGSCOUT V159: 0/1 frozen screen passes

25 Nov 2026 13:25 Cairo. Source freeze `04fddbade14839d710ca4bd0c1cbc39edde5b2ee`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 357 completed trials / 13 historical screen passes; 360 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l159_btc_observed_liquidation_proxy_recovery_1d|18|-0.134963|-0.150830|none|3.344R Standard|FAIL|

Calibrates a market-pressure model to causal labels from observed forced-order snapshots. V118 treats OI contraction as a rule without these labels, and V135/V155/V158 use margin-market comparisons without a target label.

l159_btc_observed_liquidation_proxy_recovery_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.164981/+0.066899/none; long/short -0.134963/none; fallback-rule path trades (40 repeated orders) 11, order0 0.

l159_btc_observed_liquidation_proxy_recovery_1d: pooled R23 -0.142104; fresh134 rising/sideways/falling R15 -0.173670/none/none; fresh134 R23 -0.157195; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: observed BTC COIN-M forced sell contract counts from the public archive, pinned manifest `b0c4c9dccc0598ea21587cb8b2217b4ef3b814a733205e53f4c15dc8e2d3292c`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
