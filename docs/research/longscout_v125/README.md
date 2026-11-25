# LONGSCOUT V125: 0/1 frozen screen passes

25 Nov 2026 11:25 Cairo. Source freeze `fcf7bebbc9167dc0ab3761e45e45c39ec280f9fa`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 323 completed trials / 12 historical screen passes; 326 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l125_btc_quarterly_delivery_basis_1d|0|none|none|none|0.000R Standard|FAIL|

Uses cash-and-carry basis for a future with an actual delivery date, measured against contemporaneous spot. V117/V123 use perpetual funding payments or timing, and V120 uses a perpetual premium index. Those contracts do not expire.

l125_btc_quarterly_delivery_basis_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/none/none; long/short none/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l125_btc_quarterly_delivery_basis_1d: pooled R23 none; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTCUSDT daily spot and individual quarterly-delivery closes from the public archive, pinned manifest `79095a9f1f12bbd7ddf6a363bde314f3056258e4bc8f30f58c5252f2be00f7ae`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
