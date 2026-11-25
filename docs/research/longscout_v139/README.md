# LONGSCOUT V139: 0/1 frozen screen passes

25 Nov 2026 12:16 Cairo. Source freeze `4ae541673da0cacdbbe60d94094b55a1c99dcfd5`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 337 completed trials / 12 historical screen passes; 340 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l139_btc_mining_work_recovery_1d|13|-0.005450|-0.037070|none|2.363R Standard|FAIL|

Tests recovery of estimated computational work settling the Bitcoin chain. V138 measures user demand for block space via fees and transaction count. Earlier exchange participation cases measure trading rather than mining.

l139_btc_mining_work_recovery_1d: failures trades, meanR15, early, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.096431/+0.047626/none; long/short -0.005450/none; fallback-rule path trades (40 repeated orders) 7, order0 0.

l139_btc_mining_work_recovery_1d: pooled R23 -0.013893; fresh134 rising/sideways/falling R15 -0.068753/-0.010731/none; fresh134 R23 -0.043794; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC HashRate and AssetEODCompletionTime from the public archive, pinned manifest `5bfc983595328e14a05daaf3c8c5ad25274a9f399b284b56d6278b065e443fda`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
