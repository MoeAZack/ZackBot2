# LONGSCOUT V127: 0/1 frozen screen passes

25 Nov 2026 11:35 Cairo. Source freeze `e75d83ebd518858035dd6914792f043a1a5f1708`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 325 completed trials / 12 historical screen passes; 328 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l127_btc_implied_volatility_relief_1d|14|-0.091394|-0.094015|none|2.248R Standard|FAIL|

Compares forward-looking volatility from traded options with trailing realized underlying volatility. V84/V91 use realized bar volatility, V116 historical cycle energy, and V125 dated futures basis. None uses implied volatility.

l127_btc_implied_volatility_relief_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.066477/-0.376294/none; long/short -0.091394/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l127_btc_implied_volatility_relief_1d: pooled R23 -0.101262; fresh134 rising/sideways/falling R15 -0.066504/none/none; fresh134 R23 -0.103443; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC options-derived BVOL at each completed day from the public archive, pinned manifest `e80a7ef7ff5dede66d48a5ae6ef865ea87fc3a5e6756363f6979be08a3581c50`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
