# LONGSCOUT V95: 0/2 frozen screen passes

20 Nov 2026 16:26 Cairo. Source freeze `54661c7adc83fc8223490a29d59d1eca1dfdbf5a`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 291 completed trials / 11 historical screen passes; 291 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l95_volume_leads_return_4h|144|-0.029890|-0.074617|+0.085554|15.977R Elevated|FAIL|
|r95_weekly_peer_rebalancing_4h|96|-0.076904|-0.040189|-0.224456|16.604R Elevated|FAIL|

l95_volume_leads_return_4h: failures meanR15, early, ex_best_coin, meanR23, fresh134; BTC rising/sideways/falling R15 +0.018034/-0.089972/-0.155237; long/short -0.029890/none; fallback-rule path trades (40 repeated orders) 65, order0 1.

r95_weekly_peer_rebalancing_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.087643/-0.053764/-0.110161; long/short -0.137093/-0.067680; fallback-rule path trades (40 repeated orders) 40, order0 0.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

Both frozen economic rules are rejected; no PAPER eligibility. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
