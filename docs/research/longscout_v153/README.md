# LONGSCOUT V153: 0/1 frozen screen passes

25 Nov 2026 13:05 Cairo. Source freeze `46735ea3f5ab7b7d089d6cacd44da35efe7876c6`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 351 completed trials / 13 historical screen passes; 354 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r153_btc_output_set_consolidation_1d|2|-0.615912|none|none|1.232R Standard|FAIL|

Measures the changing number of outstanding Bitcoin outputs together with counts spent. V141 assigns a share by age and amount of outputs, and V138 measures fees and transaction counts. No user or wallet identity is inferred.

r153_btc_output_set_consolidation_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.203952/-1.025704/none; long/short none/-0.615912; fallback-rule path trades (40 repeated orders) 1, order0 0.

r153_btc_output_set_consolidation_1d: pooled R23 -0.625326; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC end-of-day UTXO count and outputs spent from the public archive, pinned manifest `ffa4ca47060d683c66573d045a475a7b0b36e6110064398168a2fdd53929104d`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
