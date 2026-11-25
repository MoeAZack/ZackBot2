# LONGSCOUT V118: 0/1 frozen screen passes

25 Nov 2026 11:00 Cairo. Source freeze `8ad1062aee628631fe6a6aac5898ed649874e150`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 316 completed trials / 12 historical screen passes; 319 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l118_btc_open_interest_reset_1d|17|-0.068261|-0.184267|+0.196045|2.594R Standard|FAIL|

Uses the contraction in outstanding BTC futures contracts to identify a deleveraging event. V2 uses a BTC price flush, V93 uses linear downside beta, and V117 uses a coin’s settled funding. None measures changes in open interest.

l118_btc_open_interest_reset_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134; BTC rising/sideways/falling R15 -0.002129/-0.196813/none; long/short -0.068261/none; fallback-rule path trades (40 repeated orders) 3, order0 0.

l118_btc_open_interest_reset_1d: pooled R23 -0.077874; fresh134 rising/sideways/falling R15 -0.138420/-0.254485/none; fresh134 R23 -0.193594; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC contract open interest at 23:55 UTC from the public Metrics archive, pinned manifest `92f11f644674f029d32695dcc78335b42c01767e75476d48829e14aeb67dafcf`. Missing dates are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
