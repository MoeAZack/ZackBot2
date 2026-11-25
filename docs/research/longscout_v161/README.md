# LONGSCOUT V161: 0/1 frozen screen passes

25 Nov 2026 13:32 Cairo. Source freeze `0a2e89427cf575896ea74351945656bd95f42caf`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 359 completed trials / 13 historical screen passes; 362 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l161_alt_account_positioning_breadth_1d|14|-0.064846|-0.105906|+0.014912|3.039R Standard|FAIL|

Tests synchronized changes in account positioning across five different alt contracts. V142 compares two account cohorts inside BTC, V92 uses price breadth, and V144 uses the amount of open interest without its account side.

l161_alt_account_positioning_breadth_1d: failures trades, meanR15, late, ex_best_coin, meanR23, order0_30_per_half, fresh134; BTC rising/sideways/falling R15 -0.095473/+0.086551/none; long/short -0.064846/none; fallback-rule path trades (40 repeated orders) 6, order0 0.

l161_alt_account_positioning_breadth_1d: pooled R23 -0.073019; fresh134 rising/sideways/falling R15 -0.165771/+0.014916/none; fresh134 R23 -0.116225; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: five matched alt perpetual account-count ratios at 23:55 UTC from the public archive, pinned manifest `4376a1e63354a731d37e76ca9270acd37a6d49efc2cb15811f13862da0544b29`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
