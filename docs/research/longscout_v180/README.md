# LONGSCOUT V180: 0/1 frozen screen passes

25 Nov 2026 14:30 Cairo. Source freeze `d0c4b6e388270e38c8e7f51ea0bdca18f7691b57`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 376 completed trials / 13 historical screen passes; 381 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l180_btc_roll_spread_proxy_compression_4h|8|+0.028462|+0.095157|none|2.222R Standard|FAIL|

Separates short-run negative price covariance from similar realized volatility to track a bid-ask bounce proxy. V131 uses one- and two-day extrema, V82 uses price move per dollar, and V21 uses 4h persistence directly.

l180_btc_roll_spread_proxy_compression_4h: failures trades, late, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.066236/-0.425183/none; long/short +0.028462/none; fallback-rule path trades (40 repeated orders) 5, order0 0.

l180_btc_roll_spread_proxy_compression_4h: pooled R23 +0.001271; fresh134 rising/sideways/falling R15 +0.140866/none/none; fresh134 R23 +0.069540; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: negative five-minute return covariance and realized volatility from the public archive, pinned manifest `67c69eeb23ac8f1b889e94e45a6c306c4ede1c91c95dbb16cd6473b877bc8301`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
