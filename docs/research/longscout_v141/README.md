# LONGSCOUT V141: 0/1 frozen screen passes

25 Nov 2026 12:24 Cairo. Source freeze `85b974224c1f7239d1ad13cabe64fbd32008e21c`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 339 completed trials / 12 historical screen passes; 342 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l141_older_btc_spending_relief_1d|23|-0.098992|-0.142946|none|4.216R Standard|FAIL|

Uses the age distribution of spent Bitcoin outputs rather than transaction fees/counts, mining work or issuance on another chain. The tested input is an older-output spending share, with no assumed sale or wallet attribution.

l141_older_btc_spending_relief_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.142023/+0.022567/none; long/short -0.098992/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l141_older_btc_spending_relief_1d: pooled R23 -0.107534; fresh134 rising/sideways/falling R15 -0.171065/none/none; fresh134 R23 -0.151552; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: daily BTC output value spent and aged-at-least-365-day component from the public archive, pinned manifest `e6ac90d1ab49cdf106ea735a9360f355780669f0fb38b863e2492d7e7c770b04`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
