# LONGSCOUT V210: 0/1 frozen screen passes

25 Nov 2026 16:11 Cairo. Source freeze `6f86a3083888c4c57811a71b56a02185605c632c`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 405 completed trials / 13 historical screen passes; 410 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l210_spx_skew_relief_against_vix_rise_1d|2|-0.529280|none|none|1.059R Standard|FAIL|

Tests opposing repricing of asymmetry and dispersion in SPX options. V174 compares implied dispersion at two maturities. V185 uses dispersion of VIX options while SPX implied dispersion stays stable.

l210_spx_skew_relief_against_vix_rise_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/-0.529280/none; long/short -0.529280/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l210_spx_skew_relief_against_vix_rise_1d: pooled R23 -0.534520; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Cboe SKEW and VIX paired US session closes from the public archive, pinned manifest `9ffbc0f817b60ab287db0109b6e634c9d050ae077a0ee11db279f2ceb9ade45f`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
