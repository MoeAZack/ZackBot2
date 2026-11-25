# LONGSCOUT V202: 0/1 frozen screen passes

25 Nov 2026 15:44 Cairo. Source freeze `bc175b05e8b41af4ce755466a93bfa722d283c26`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 397 completed trials / 13 historical screen passes; 402 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l202_btc_fear_relief_surprise_1d|5|+0.028396|-0.181044|none|0.904R Standard|FAIL|

Tests deviation from a rolling price-based expectation of a published BTC fear composite. V194 uses Wikipedia article reads, not index changes or price residuals.

l202_btc_fear_relief_surprise_1d: failures trades, early, ex_best_coin, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 +0.137040/-0.047896/none; long/short +0.028396/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l202_btc_fear_relief_surprise_1d: pooled R23 +0.020887; fresh134 rising/sideways/falling R15 none/-0.209025/none; fresh134 R23 -0.190387; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Alternative.me Bitcoin Fear & Greed Index from the public archive, pinned manifest `40bf10237963f8bd4e9faded538c1da3538fc6fee91586fa5fe55974742dcc10`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
