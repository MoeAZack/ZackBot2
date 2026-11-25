# LONGSCOUT V165: 0/1 frozen screen passes

25 Nov 2026 13:44 Cairo. Source freeze `d727373a250b40e28890cdd4d08220f6fa0eab8b`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 362 completed trials / 13 historical screen passes; 366 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l165_btc_mempool_queue_clearance_1d|29|-0.060583|-0.059310|-0.070421|4.733R Standard|FAIL|

Measures the queue of unconfirmed Bitcoin transactions while holding their arrival rate near normal. V138 uses fees paid by confirmed transactions and V153 uses already spent or unspent confirmed outputs.

l165_btc_mempool_queue_clearance_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.086747/+0.104035/none; long/short -0.060583/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l165_btc_mempool_queue_clearance_1d: pooled R23 -0.068431; fresh134 rising/sideways/falling R15 -0.068707/none/none; fresh134 R23 -0.067111; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: mempool pending count and incoming virtual bytes per second from the public archive, pinned manifest `6993289319e3751be3dd3b64f8b8cbb0c42f0703813741287d4223a850e4ff41`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
