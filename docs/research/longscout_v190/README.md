# LONGSCOUT V190: 0/1 frozen screen passes

25 Nov 2026 15:03 Cairo. Source freeze `2008d30194ed632c0ad6df79cbd31af593e27351`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 386 completed trials / 13 historical screen passes; 391 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r190_cme_option_equivalent_oi_relief_1d|6|-0.161885|-0.135205|none|1.435R Standard|FAIL|

Infers gross delta-equivalent options exposure from paired combined and futures-only reports. V176 looks at futures trader direction, and V127 and V185 use options prices rather than outstanding exposure.

r190_cme_option_equivalent_oi_relief_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.161885/none/none; long/short -0.433244/+0.070787; fallback-rule path trades (40 repeated orders) 0, order0 0.

r190_cme_option_equivalent_oi_relief_1d: pooled R23 -0.169370; fresh134 rising/sideways/falling R15 -0.135205/none/none; fresh134 R23 -0.142893; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: CFTC CME Bitcoin options-combined versus futures-only open interest from the public archive, pinned manifest `a5934304d6266ea78e383866184af66233861d41aed8fddba7d888bafd12f830`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
