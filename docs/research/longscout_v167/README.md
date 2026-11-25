# LONGSCOUT V167: 0/1 frozen screen passes

25 Nov 2026 13:51 Cairo. Source freeze `f50914997da859146d3f2c9f85ba04f73bcaf627`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 364 completed trials / 13 historical screen passes; 368 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r167_btc_mempool_priority_composition_1d|9|-0.123739|-0.108585|none|1.759R Standard|FAIL|

Measures urgency as the fee composition of unconfirmed virtual bytes with stable queue size. V165 uses the count of pending transactions and their arrival rate. V138 uses fees already paid by confirmed transactions.

r167_btc_mempool_priority_composition_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.100168/-0.106146/none; long/short +0.048160/-0.319012; fallback-rule path trades (40 repeated orders) 0, order0 0.

r167_btc_mempool_priority_composition_1d: pooled R23 -0.131773; fresh134 rising/sideways/falling R15 -0.097643/-0.147064/none; fresh134 R23 -0.117329; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: sampled vbytes at and above 10 sat/vB and total vbytes from the public archive, pinned manifest `e9cb4d12879f8f814ea48afe2af30227a2429c055f303bcc0438a28c21b89e7b`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
