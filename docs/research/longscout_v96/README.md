# LONGSCOUT V96: 0/2 frozen screen passes

20 Nov 2026 16:45 Cairo. Source freeze `b6156496fc6df0e96eb65575b7086b3c0eda1d42`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 293 completed trials / 11 historical screen passes; 293 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l96_sequential_cusum_4h|156|+0.031197|-0.034840|+0.235522|11.725R Standard|FAIL|
|r96_ordinal_oscillation_4h|0|none|none|none|0.000R Standard|FAIL|

l96_sequential_cusum_4h: failures fresh134; BTC rising/sideways/falling R15 +0.082838/+0.174274/-0.258014; long/short +0.031197/none; fallback-rule path trades (40 repeated orders) 95, order0 2.

r96_ordinal_oscillation_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/none/none; long/short none/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
