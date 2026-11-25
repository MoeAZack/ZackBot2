# LONGSCOUT V156: 0/1 frozen screen passes

25 Nov 2026 13:17 Cairo. Source freeze `44fb3276e81c267f5f1fd5992d2cb78c41e66915`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 354 completed trials / 13 historical screen passes; 357 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r156_btc_settled_funding_premium_divergence_4h|0|none|none|none|0.000R Standard|FAIL|

Pairs a past settled payment with the opposite sign of the current premium on the same BTC contract. V120 uses premium convergence alone, V143 compares funding between coins, and V135 compares two funding payments.

r156_btc_settled_funding_premium_divergence_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/none/none; long/short none/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

r156_btc_settled_funding_premium_divergence_4h: pooled R23 none; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: public Funding Rate archive, pinned manifest `324c4ebf7f05798f74e74ed9772310ea44c0216efe1db8b4d6786950a619e691`. The 166-symbol release masks missing-cadence windows. Simulator funding costs remain fixed.

Entry feature: BTC 4h premium-index closes from the public archive, pinned manifest `c28f63707a0531074df43357e3a74a16a0f8b068275f73bac31cf2a69df7b300`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
