# LONGSCOUT V197: 0/1 frozen screen passes

25 Nov 2026 15:26 Cairo. Source freeze `389452d9e07b483a5dac249de642aa1ac191f86a`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 393 completed trials / 13 historical screen passes; 398 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l197_chicago_fed_credit_subindex_easing_1d|2|+0.012570|+0.012570|none|0.324R Standard|FAIL|

Uses vintage-specific credit conditions when the financial risk subindex is stable. V173 uses Treasury real yields, V175 repo execution costs, and V182 Fed account balances, without modeling this credit component.

l197_chicago_fed_credit_subindex_easing_1d: failures trades, late, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.012570/none/none; long/short +0.012570/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l197_chicago_fed_credit_subindex_easing_1d: pooled R23 +0.006767; fresh134 rising/sideways/falling R15 +0.012570/none/none; fresh134 R23 +0.006767; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: vintage-matched Chicago Fed credit and risk subindexes from the public archive, pinned manifest `46652503c9d97a4d6aad1e4d160500190e24bc9afa831455c8ceef84e920ec6b`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
