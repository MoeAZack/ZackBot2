# LONGSCOUT V160: 0/1 frozen screen passes

25 Nov 2026 13:29 Cairo. Source freeze `1d6bfb3bc8d4f0c1fbc95e8385aba04314297817`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 358 completed trials / 13 historical screen passes; 361 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r160_cross_coin_residual_reversal_1d|1|-0.094707|-0.094707|none|0.095R Standard|FAIL|

Learns a lead-response slope from other coins’ already completed next-day returns and applies it to a held-out instrument. V97 thresholds its own BTC residual, V94 uses concurrent cross-sectional dispersion, and V133 matches ordered paths within the same instrument.

r160_cross_coin_residual_reversal_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.094707/none/none; long/short none/-0.094707; fallback-rule path trades (40 repeated orders) 0, order0 0.

r160_cross_coin_residual_reversal_1d: pooled R23 -0.102583; fresh134 rising/sideways/falling R15 -0.094707/none/none; fresh134 R23 -0.102583; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
