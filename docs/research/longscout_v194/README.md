# LONGSCOUT V194: 0/1 frozen screen passes

25 Nov 2026 15:17 Cairo. Source freeze `3d8e71929fbcc844984ef5e20627ff556dbf1576`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 390 completed trials / 13 historical screen passes; 395 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l194_ethereum_wikimedia_attention_spike_1d|6|-0.036685|-0.118261|none|1.122R Standard|FAIL|

Uses external information-seeking activity from Wikimedia. V36 and V94 proxy attention with traded crypto volume and V106 learns price weekday effects.

l194_ethereum_wikimedia_attention_spike_1d: failures trades, meanR15, early, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.110849/+0.047102/none; long/short -0.036685/none; fallback-rule path trades (40 repeated orders) 4, order0 0.

l194_ethereum_wikimedia_attention_spike_1d: pooled R23 -0.045800; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 -0.126748; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: English Wikipedia user pageviews for ETH, BTC and Stock_market from the public archive, pinned manifest `eba1cc228e91b737d6981b328e80adb5dd7f28b402251a6b75c29ab9b611734e`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
