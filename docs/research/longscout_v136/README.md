# LONGSCOUT V136: 0/1 frozen screen passes

25 Nov 2026 12:07 Cairo. Source freeze `9faed3ac92ed316b57e4b36ec78c2e511bb27bf3`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 334 completed trials / 12 historical screen passes; 337 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l136_btc_taker_flow_reversal_4h|1|-1.037614|none|none|1.038R Standard|FAIL|

Uses a chronological reversal in actual market-wide BTC taker direction. V98 is single-day per-coin absorption, where price opposes takers, or trade-size reversal. V122 splits unsigned activity across instruments. This neither tests absorption nor estimates trade size.

l136_btc_taker_flow_reversal_4h: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -1.037614/none/none; long/short -1.037614/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l136_btc_taker_flow_reversal_4h: pooled R23 -1.054069; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC 4h taker-buy and total quote turnover from the public archive, pinned manifest `c85bb28a5123b939ffee8ebc836066a169c2dc65b0c77dc0eb67a635ac8056e7`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
