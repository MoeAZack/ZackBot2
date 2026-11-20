# LONGSCOUT V101: 0/2 frozen screen passes

20 Nov 2026 17:34 Cairo. Source freeze `5a4b103e63dd06df3d7b9635ce58a4da9950b611`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 301 completed trials / 11 historical screen passes; 301 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l101_peer_tail_isolation_1d|8|+0.413538|+0.413538|none|1.129R Standard|FAIL|
|r101_relative_peer_ou_1d|132|-0.036323|-0.034873|-0.066726|9.910R Standard|FAIL|

l101_peer_tail_isolation_1d: failures trades, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.222844/+0.349566/+0.689122; long/short +0.413538/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

r101_relative_peer_ou_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.023053/-0.035892/-0.189762; long/short -0.066642/-0.026706; fallback-rule path trades (40 repeated orders) 65, order0 1.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
