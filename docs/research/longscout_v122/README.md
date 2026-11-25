# LONGSCOUT V122: 0/1 frozen screen passes

25 Nov 2026 11:14 Cairo. Source freeze `bc8b6e32049e5191aa5c0aa41a8bffc1276fc81f`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 320 completed trials / 12 historical screen passes; 323 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l122_btc_spot_turnover_lead_4h|19|-0.012046|-0.056662|none|3.551R Standard|FAIL|

Tests the allocation of same-quote BTC turnover between spot and perpetual trading. V112 changes the clock using a single market’s turnover, V98 uses taker flows, and V120 uses the premium index. No prior family attributes a rise in activity to spot relative to perpetual trading.

l122_btc_spot_turnover_lead_4h: failures trades, meanR15, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.378479/+0.428166/none; long/short -0.012046/none; fallback-rule path trades (40 repeated orders) 14, order0 0.

l122_btc_spot_turnover_lead_4h: pooled R23 -0.038045; fresh134 rising/sideways/falling R15 -0.371263/none/none; fresh134 R23 -0.076083; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTCUSDT spot 4h quote turnover from the public archive, pinned manifest `88b2035e35ca42dcd224237331356fe090190bfbf3689aabcf362d78b576a58a`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
