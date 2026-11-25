# LONGSCOUT V146: 0/1 frozen screen passes

25 Nov 2026 12:42 Cairo. Source freeze `ccf5ba13723afdeb7fb5be60db05c6c493c9af83`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 344 completed trials / 13 historical screen passes; 347 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l146_btc_oi_turnover_capacity_recovery_1d|5|+0.174413|none|none|0.925R Standard|FAIL|

Uses the stock of BTC open positions against observed turnover in the same base units. V118 requires open interest to contract; this tests increased trading capacity with rising open interest. V144 compares only the stock of open contracts against two other markets.

l146_btc_oi_turnover_capacity_recovery_1d: failures trades, ex_best_coin, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.103671/+0.774571/none; long/short +0.174413/none; fallback-rule path trades (40 repeated orders) 4, order0 0.

l146_btc_oi_turnover_capacity_recovery_1d: pooled R23 +0.165568; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC contract open interest at 23:55 UTC from the public Metrics archive, pinned manifest `92f11f644674f029d32695dcc78335b42c01767e75476d48829e14aeb67dafcf`. Missing dates are masked.

Entry feature: BTC open-interest quantity relative to frozen daily base-unit turnover from the public archive, pinned manifest `92f11f644674f029d32695dcc78335b42c01767e75476d48829e14aeb67dafcf`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
