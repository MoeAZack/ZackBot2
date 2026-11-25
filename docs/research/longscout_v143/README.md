# LONGSCOUT V143: 1/1 frozen screen passes

25 Nov 2026 12:31 Cairo. Source freeze `e9b78531a73152acc353d1d37afa6b3946fb3756`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 341 completed trials / 13 historical screen passes; 344 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r143_relative_funding_carry_fade_1d|232|+0.029548|+0.013071|+0.077252|6.571R Standard|WIDE165_PASS|

Fades relative carry extremes among native contracts. V117 buys an absolute negative funding squeeze with a price breakout; V123 tests cadence; V135 tests two BTC collateral legs. V108 ranks past returns, not relative carry.

r143_relative_funding_carry_fade_1d: failures none; BTC rising/sideways/falling R15 -0.002115/+0.105356/none; long/short +0.053159/-0.037394; fallback-rule path trades (40 repeated orders) 87, order0 2.

r143_relative_funding_carry_fade_1d: pooled R23 +0.020717; fresh134 rising/sideways/falling R15 +0.002633/+0.032941/none; fresh134 R23 +0.004529; fresh134 top-three/net undefined; forward bar FAIL (top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: public Funding Rate archive, pinned manifest `324c4ebf7f05798f74e74ed9772310ea44c0216efe1db8b4d6786950a619e691`. The 166-symbol release masks missing-cadence windows. Simulator funding costs remain fixed.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

A screen pass is a lead for independent reproduction. No book is activated.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
