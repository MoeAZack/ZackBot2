# LONGSCOUT V94: 0/2 frozen screen passes

20 Nov 2026 16:17 Cairo. Source freeze `91a6775d1eb0558b5de44086e2b9189d7aed4579`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 289 completed trials / 11 historical screen passes; 289 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l94_peer_attention_rotation_4h|163|-0.085328|-0.094247|-0.076779|22.294R Elevated|FAIL|
|r94_peer_dispersion_shock_fade_4h|26.5|+0.168273|+0.114433|+0.053115|4.741R Standard|FAIL|

l94_peer_attention_rotation_4h: failures meanR15, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.091613/+0.033687/-0.125937; long/short -0.085328/none; fallback-rule path trades (40 repeated orders) 29, order0 0.

r94_peer_dispersion_shock_fade_4h: failures trades, order0_30_per_half; BTC rising/sideways/falling R15 +0.154197/+0.109146/+0.203316; long/short +0.365631/+0.042152; fallback-rule path trades (40 repeated orders) 0, order0 0.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
