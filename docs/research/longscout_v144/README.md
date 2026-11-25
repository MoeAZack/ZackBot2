# LONGSCOUT V144: 0/1 frozen screen passes

25 Nov 2026 12:37 Cairo. Source freeze `8d25279329bed25a1ac27801983daeceb2b69b99`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 342 completed trials / 13 historical screen passes; 345 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l144_native_open_interest_rotation_1d|20|-0.305519|-0.303233|-0.213181|6.579R Standard|FAIL|

Requires open-interest quantity to expand simultaneously in two futures markets while contracting in BTC. V118 uses a BTC-only reset; V94/V130 observe turnover; V134 compares option volatility. This compares changes within each instrument, without asserting that capital moved between them.

l144_native_open_interest_rotation_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.235241/-0.583328/none; long/short -0.305519/none; fallback-rule path trades (40 repeated orders) 11, order0 0.

l144_native_open_interest_rotation_1d: pooled R23 -0.313584; fresh134 rising/sideways/falling R15 -0.241504/none/none; fresh134 R23 -0.311557; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTCUSDT, ETHUSDT and SOLUSDT native open interest at 23:55 UTC from the public archive, pinned manifest `a82581631330b7dfcd71bee71a541c7ce716e69fc3e73c1abd7903f0f2c67526`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
