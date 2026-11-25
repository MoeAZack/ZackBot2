# LONGSCOUT V135: 0/1 frozen screen passes

25 Nov 2026 12:02 Cairo. Source freeze `31150f62b95f50d19b50674f5214f755b2245bcc`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 333 completed trials / 12 historical screen passes; 336 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l135_btc_collateral_funding_divergence_4h|4|+0.112719|+0.122544|none|1.022R Standard|FAIL|

Compares compensation in coin collateral with USDT collateral for the same underlying at a matched settlement time. V117 uses single-contract signed funding and V123 changes in cadence. This tests divergence between margin assets.

l135_btc_collateral_funding_divergence_4h: failures trades, late, ex_best_coin, order0_30_per_half, extra31; BTC rising/sideways/falling R15 -0.350034/+0.610235/none; long/short +0.112719/none; fallback-rule path trades (40 repeated orders) 2, order0 0.

l135_btc_collateral_funding_divergence_4h: pooled R23 +0.099680; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 +0.110112; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC COIN-M and USD-M matched funding rates from the public archive, pinned manifest `5d99bf116b00cb9f342d9d80aa1c9c765ed8f9dd26eeb28ca80931ba0c967c0b`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
