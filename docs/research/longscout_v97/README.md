# LONGSCOUT V97: 0/2 frozen screen passes

20 Nov 2026 17:02 Cairo. Source freeze `1eddbbefc5b77f34818d1c003c8400eb34537971`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 295 completed trials / 11 historical screen passes; 295 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l97_quiet_btc_residual_flush_4h|95|+0.024482|+0.023423|-0.002938|10.883R Standard|FAIL|
|l97_intraday_participation_disagreement_4h|54|-0.124932|-0.099437|-0.313403|9.332R Standard|FAIL|

l97_quiet_btc_residual_flush_4h: failures trades, late, ex_best_coin, extra31; BTC rising/sideways/falling R15 -0.100580/+0.058684/+0.391986; long/short +0.024482/none; fallback-rule path trades (40 repeated orders) 45, order0 1.

l97_intraday_participation_disagreement_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.163854/-0.103049/+0.487248; long/short -0.124932/none; fallback-rule path trades (40 repeated orders) 48, order0 1.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
