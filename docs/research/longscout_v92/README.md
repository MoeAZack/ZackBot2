# LONGSCOUT V92: 0/2 frozen screen passes

20 Nov 2026 15:56 Cairo. Source freeze `83b80a32110714face118034ca741ee77b2f4536`; all cases frozen and pushed before outcomes. Ledger: 285 completed trials / 11 historical screen passes; 285 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l92_market_breadth_thrust_4h|65|-0.077669|-0.087514|-0.258111|11.193R Standard|FAIL|
|r92_intraday_extreme_timing_4h|207|-0.122613|-0.035146|-0.332503|34.146R High|FAIL|

l92_market_breadth_thrust_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.106018/+0.694432/-0.224176; long/short -0.077669/none; fallback-rule path trades (40 repeated orders) 35, order0 0.

r92_intraday_extreme_timing_4h: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.009012/-0.347253/-0.152592; long/short -0.098913/-0.140709; fallback-rule path trades (40 repeated orders) 31, order0 1.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
