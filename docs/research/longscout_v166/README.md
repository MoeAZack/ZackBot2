# LONGSCOUT V166: 0/1 frozen screen passes

25 Nov 2026 13:48 Cairo. Source freeze `cd505767ff1e4298bb45d7aea3efc9e6a1781350`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 363 completed trials / 13 historical screen passes; 367 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l166_btc_bid_resilience_during_selling_1d|0|none|none|none|0.000R Standard|FAIL|

Re-registration of the unrun V163 hypothesis after an input schema failure. Tests an increase in displayed bids during observed sell aggression, with ask quantity as control. V124 compares near and outer depth shares, V150 uses gross capacity, and V136 tests a reversal in executed flow alone.

l166_btc_bid_resilience_during_selling_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 none/none/none; long/short none/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l166_btc_bid_resilience_during_selling_1d: pooled R23 none; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: paired native BTC bid and ask depth within 1% of mid from the public archive, pinned manifest `c1ca4058b552328308ba0915236179e299fed809f8056771fa53e1eba7e36d1e`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
