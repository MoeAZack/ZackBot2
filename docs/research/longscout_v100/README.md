# LONGSCOUT V100: 0/2 frozen screen passes

20 Nov 2026 17:24 Cairo. Source freeze `a4281cac7cb54e5968aebcdedec7b1f8ad4ff48c`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 299 completed trials / 11 historical screen passes; 299 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l100_downside_range_response_4h|100|-0.063402|-0.065368|-0.011308|12.277R Standard|FAIL|
|r100_distribution_migration_4h|70|-0.138336|-0.144182|-0.158389|12.478R Standard|FAIL|

l100_downside_range_response_4h: failures meanR15, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.058056/+0.238096/-0.343118; long/short -0.063402/none; fallback-rule path trades (40 repeated orders) 43, order0 2.

r100_distribution_migration_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.153874/-0.208243/+0.007148; long/short +0.230837/-0.270481; fallback-rule path trades (40 repeated orders) 92, order0 3.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
