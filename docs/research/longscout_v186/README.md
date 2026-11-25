# LONGSCOUT V186: 0/1 frozen screen passes

25 Nov 2026 14:49 Cairo. Source freeze `af71dcac99b1d8e84c9fb608560c743c7f67d8e4`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 382 completed trials / 13 historical screen passes; 387 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l186_same_usdc_lending_rate_gap_relief_1d|18|+0.105746|+0.088313|none|2.255R Standard|FAIL|

Tests cross-protocol relief in same-token Ethereum lending rates, excluding reward incentives. V179 measures lending quantities, V175 measures the distribution within Treasury repo and V143 compares futures funding.

l186_same_usdc_lending_rate_gap_relief_1d: failures trades, order0_30_per_half, extra31; BTC rising/sideways/falling R15 +0.116206/+0.096260/none; long/short +0.105746/none; fallback-rule path trades (40 repeated orders) 7, order0 0.

l186_same_usdc_lending_rate_gap_relief_1d: pooled R23 +0.095498; fresh134 rising/sideways/falling R15 +0.070950/+0.097598/none; fresh134 R23 +0.079373; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Aave V3 and Compound V3 Ethereum USDC base supply APY from the public archive, pinned manifest `ecfa62df0d511f3e7e6fb7fec79175f5bf3d61bbb02b3aea8c63dcc36e1b28e3`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
