# LONGSCOUT V152: 0/1 frozen screen passes

25 Nov 2026 13:01 Cairo. Source freeze `a2806c2492e585210d9a8074aaca0c68143cf767`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 350 completed trials / 13 historical screen passes; 353 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l152_btc_protocol_reward_retarget_1d|7|-0.202723|-0.191822|none|2.191R Standard|FAIL|

Uses changes to a protocol-set reward per unit expected work, excluding fee income and fiat price. V139 measures recovery in observed hash activity, which can fall while the retarget raises the base reward per unit work.

l152_btc_protocol_reward_retarget_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 +0.009787/-0.777399/none; long/short -0.202723/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l152_btc_protocol_reward_retarget_1d: pooled R23 -0.212296; fresh134 rising/sideways/falling R15 -0.040120/none/none; fresh134 R23 -0.201818; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC end-of-day block height and difficulty from the public archive, pinned manifest `0c58e2c1246e1f20c7386de4e77ea44e21ac7bfd3ff6e6650c77769b5e759cc9`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
