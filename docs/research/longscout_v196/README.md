# LONGSCOUT V196: 0/1 frozen screen passes

25 Nov 2026 15:22 Cairo. Source freeze `6b3a49b79980d70d3bc96adadde2493761f5646e`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 392 completed trials / 13 historical screen passes; 397 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l196_btc_transactions_per_stable_block_weight_1d|13|-0.256804|-0.208896|none|3.830R Standard|FAIL|

Tests transaction count per physical chain weight and steady block production. V138 measures fees paid, V183 header timing and V153 output churn.

l196_btc_transactions_per_stable_block_weight_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.254625/-0.242701/none; long/short -0.256804/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l196_btc_transactions_per_stable_block_weight_1d: pooled R23 -0.265851; fresh134 rising/sideways/falling R15 -0.248352/none/none; fresh134 R23 -0.217838; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Bitcoin block weight, block count and transaction count from the public archive, pinned manifest `1a19ad1e38c083f69df73d28ad41baa5398d54ed7731341e7b1cbe258998cff8`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
