# LONGSCOUT V189: 0/1 frozen screen passes

25 Nov 2026 15:00 Cairo. Source freeze `7b882cc2b8c356d770fecbc71deee6a0f09287d0`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 385 completed trials / 13 historical screen passes; 390 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l189_btc_realized_cap_acceleration_1d|4|-0.083456|+0.027078|none|1.028R Standard|FAIL|

Tests repricing of UTXO-weighted realized capitalization with a constrained spot-valued cap move. V141 uses spent-output age and V153 output counts, without assigning historical prices to the UTXO stock.

l189_btc_realized_cap_acceleration_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, extra31; BTC rising/sideways/falling R15 -0.083456/none/none; long/short -0.083456/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l189_btc_realized_cap_acceleration_1d: pooled R23 -0.095711; fresh134 rising/sideways/falling R15 +0.027078/none/none; fresh134 R23 +0.015843; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Bitcoin realized and market capitalizations from the public archive, pinned manifest `e43260419875058f3c284496a391ffc91862073e0225b74f4d8f0fc873016cf3`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
