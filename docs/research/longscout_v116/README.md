# LONGSCOUT V116: 0/1 frozen screen passes

25 Nov 2026 10:49 Cairo. Source freeze `d20b963bb072fb3c02071e02f6df118135b6af33`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 314 completed trials / 12 historical screen passes; 317 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r116_daily_spectral_cycle_fade_1d|10|+0.062050|+0.070047|+0.070473|2.035R Standard|FAIL|

Locates the turning phase of a dominant frequency in a completed price history. V21 measures one-lag autocorrelation, V81 compares time-domain variances, and V96 counts ordinal patterns. None fits and trades the phase of an observed cycle.

r116_daily_spectral_cycle_fade_1d: failures trades, ex_best_coin, order0_30_per_half; BTC rising/sideways/falling R15 none/+0.062050/none; long/short +0.011015/+0.158809; fallback-rule path trades (40 repeated orders) 0, order0 0.

r116_daily_spectral_cycle_fade_1d: pooled R23 +0.054072; fresh134 rising/sideways/falling R15 none/+0.070047/none; fresh134 R23 +0.062262; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
