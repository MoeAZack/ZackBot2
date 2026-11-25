# LONGSCOUT V128: 0/1 frozen screen passes

25 Nov 2026 11:39 Cairo. Source freeze `20579032160939f23eae798147fb506b3ab3c305`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 326 completed trials / 12 historical screen passes; 329 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l128_btc_trade_only_downside_extreme_4h|51|+0.117430|+0.138437|-0.001357|6.012R Standard|FAIL|

Compares actual trade extremes against simultaneous-period mark and index envelopes. V92/V109 use the chronology of traded extremes and V120 uses changes in the premium index close. This tests trade-only range excess.

l128_btc_trade_only_downside_extreme_4h: failures trades, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.108152/+0.066978/none; long/short +0.117430/none; fallback-rule path trades (40 repeated orders) 11, order0 0.

l128_btc_trade_only_downside_extreme_4h: pooled R23 +0.097649; fresh134 rising/sideways/falling R15 +0.145447/+0.091741/none; fresh134 R23 +0.119002; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC 4h mark and index high/low/close envelopes from the public archive, pinned manifest `74bee3f375a9401395256e0412be16c09e046ecf1cbf4dff5727f60e30a2c2b5`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
