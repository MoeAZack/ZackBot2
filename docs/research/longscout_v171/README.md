# LONGSCOUT V171: 0/1 frozen screen passes

25 Nov 2026 14:05 Cairo. Source freeze `cfdbf10a4e93eb57cbb8ad304f01721c5d24c324`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 367 completed trials / 13 historical screen passes; 372 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l171_btc_flow_response_asymmetry_4h|56|+0.012955|-0.041690|+0.185460|7.117R Standard|FAIL|

Compares estimated price sensitivity to buyer- and seller-initiated flow. V82 uses unsigned realized impact, V136 uses a chronological reversal in flow, and V98 conditions on one bar of passive absorption.

l171_btc_flow_response_asymmetry_4h: failures trades, early, ex_best_coin, meanR23, order0_30_per_half, fresh134; BTC rising/sideways/falling R15 +0.011823/-0.012046/none; long/short +0.012955/none; fallback-rule path trades (40 repeated orders) 24, order0 0.

l171_btc_flow_response_asymmetry_4h: pooled R23 -0.010221; fresh134 rising/sideways/falling R15 -0.024095/-0.057002/none; fresh134 R23 -0.062937; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC taker-buy and total quote turnover from the public archive, pinned manifest `c85bb28a5123b939ffee8ebc836066a169c2dc65b0c77dc0eb67a635ac8056e7`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
