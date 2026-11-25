# LONGSCOUT V131: 0/1 frozen screen passes

25 Nov 2026 11:47 Cairo. Source freeze `698a558b3858d42218d86d3972141dd19cd86900`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 329 completed trials / 12 historical screen passes; 332 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l131_estimated_spread_shock_recovery_1d|149|-0.054590|-0.049872|-0.056292|12.395R Standard|FAIL|

Tests a transaction-spread proxy from two-day versus single-day ranges, holding the current daily range above its recent norm. V82 estimates price impact per dollar of volume and V91 uses realized volatility. Neither separates spread from underlying range using the two-day relationship.

l131_estimated_spread_shock_recovery_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.039624/-0.066803/none; long/short -0.054590/none; fallback-rule path trades (40 repeated orders) 56, order0 0.

l131_estimated_spread_shock_recovery_1d: pooled R23 -0.063450; fresh134 rising/sideways/falling R15 -0.046522/-0.078571/none; fresh134 R23 -0.058829; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
