# LONGSCOUT V93: 0/2 frozen screen passes

20 Nov 2026 16:04 Cairo. Source freeze `c357f3960b8838d7affa7b704d0115209da8d07d`; all cases frozen and pushed before outcomes. Ledger: 287 completed trials / 11 historical screen passes; 287 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l93_downside_beta_rebound_4h|129|-0.086662|-0.088203|-0.026460|22.873R Elevated|FAIL|
|r93_halfday_displacement_reversal_4h|1177|-0.061303|-0.051033|-0.095762|76.380R Extreme|FAIL|

l93_downside_beta_rebound_4h: failures meanR15, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.026233/-0.051287/-0.187798; long/short -0.086662/none; fallback-rule path trades (40 repeated orders) 51, order0 0.

r93_halfday_displacement_reversal_4h: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.059591/-0.077772/-0.056117; long/short -0.058727/-0.063639; fallback-rule path trades (40 repeated orders) 707, order0 15.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
