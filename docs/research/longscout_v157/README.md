# LONGSCOUT V157: 0/1 frozen screen passes

25 Nov 2026 13:19 Cairo. Source freeze `23080d00af499cf4e34e3f9ef3507fdde624fa7d`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 355 completed trials / 13 historical screen passes; 358 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l157_relative_wrapped_btc_supply_recovery_1d|1|+0.146458|+0.146458|none|0.000R Standard|FAIL|

Compares growth in a backed BTC representation with growth in underlying BTC supply. V140 tests expansion in dollar tokens on one chain, V141 studies the age of native outputs and V152 tests scheduled compensation per work.

l157_relative_wrapped_btc_supply_recovery_1d: failures trades, late, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.146458/none/none; long/short +0.146458/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l157_relative_wrapped_btc_supply_recovery_1d: pooled R23 +0.136015; fresh134 rising/sideways/falling R15 +0.146458/none/none; fresh134 R23 +0.136015; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Coin Metrics WBTC and BTC supply with completion times from the public archive, pinned manifest `b4ab6cdfc1ff2b605f9d8f36307797716e0f4c39ff8c4b407ddf2b2b455510dc`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
