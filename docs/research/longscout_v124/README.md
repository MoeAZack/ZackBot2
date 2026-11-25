# LONGSCOUT V124: 0/1 frozen screen passes

25 Nov 2026 11:22 Cairo. Source freeze `f93dcccf038c4ef7dc6a305828d42338d98e61a9`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 322 completed trials / 12 historical screen passes; 325 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r124_btc_orderbook_depth_redistribution_1d|37|-0.032623|-0.000042|-0.086949|4.297R Standard|FAIL|

Measures where displayed liquidity is placed relative to the midpoint, comparing the near and outer book. V98 is about aggressive trades, V118 total open interest, and V119 top-trader positions. Those do not observe the shape of resting depth.

r124_btc_orderbook_depth_redistribution_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.006766/-0.062756/none; long/short +0.120362/-0.061264; fallback-rule path trades (40 repeated orders) 37, order0 1.

r124_btc_orderbook_depth_redistribution_1d: pooled R23 -0.042384; fresh134 rising/sideways/falling R15 +0.037394/-0.084498/none; fresh134 R23 -0.009453; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC near versus outer bid-depth share from the public archive, pinned manifest `b82f3e519220719f57544e64fca068c69634615b5cf55774d06016825c48e47e`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
