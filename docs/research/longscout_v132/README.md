# LONGSCOUT V132: 0/1 frozen screen passes

25 Nov 2026 11:51 Cairo. Source freeze `cd1da98ce0eb6b17ed8d7295fd8aae43f5ff578d`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 330 completed trials / 12 historical screen passes; 333 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l132_spot_triangle_convergence_4h|1|+1.948427|none|none|0.000R Standard|FAIL|

Uses the no-arbitrage relation among three simultaneous traded spot markets. V121 observes the stablecoin rate itself, and V120/V125 observe derivative pricing. The triangular residual removes the stablecoin cross-rate.

l132_spot_triangle_convergence_4h: failures trades, late, ex_best_coin, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 +1.948427/none/none; long/short +1.948427/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l132_spot_triangle_convergence_4h: pooled R23 +1.928084; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTCUSDT/BTCUSDC/USDCUSDT synchronized triangular close residual from the public archive, pinned manifest `76336cedfbe11b006f081d6b8b06d6d812fb2ac9959a5dd39ab8771e0b0516c8`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
