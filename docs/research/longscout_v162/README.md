# LONGSCOUT V162: 0/1 frozen screen passes

25 Nov 2026 13:34 Cairo. Source freeze `9e2d11a47bfadb4ff6fc0466187f697e1e765ded`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 360 completed trials / 13 historical screen passes; 363 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l162_eth_active_address_breadth_1d|18|+0.068329|+0.045600|none|1.754R Standard|FAIL|

Tests whether more unique ETH addresses appear at comparable transaction throughput. V138 tests fee pressure, V140 tests token supply, and V130 tests the breadth of liquid futures contracts.

l162_eth_active_address_breadth_1d: failures trades, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 -0.016737/+0.290295/none; long/short +0.068329/none; fallback-rule path trades (40 repeated orders) 16, order0 0.

l162_eth_active_address_breadth_1d: pooled R23 +0.059641; fresh134 rising/sideways/falling R15 -0.044081/+0.300660/none; fresh134 R23 +0.036874; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: ETH active address count and transaction count from the public archive, pinned manifest `5e9f14fc3aa5ea4128234cac029ed68021a850f483a5e3d569497f6f5832475e`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
