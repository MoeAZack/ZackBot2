# LONGSCOUT V178: 0/1 frozen screen passes

25 Nov 2026 14:23 Cairo. Source freeze `6d8b66d4b3112b4d210db00490689b256c358581`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 374 completed trials / 13 historical screen passes; 379 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l178_rollup_dex_share_rotation_long_1d|7|-0.011237|+0.006940|none|1.083R Standard|FAIL|

Tracks separately reported spot DEX turnover allocation across Ethereum and two rollups. V162 uses ETH active addresses, V122 is Binance spot vs perpetual turnover, and V164 uses Lightning channel capacity.

l178_rollup_dex_share_rotation_long_1d: failures trades, meanR15, late, ex_best_coin, meanR23, order0_30_per_half, extra31; BTC rising/sideways/falling R15 -0.020401/+0.211789/none; long/short -0.011237/none; fallback-rule path trades (40 repeated orders) 4, order0 0.

l178_rollup_dex_share_rotation_long_1d: pooled R23 -0.017666; fresh134 rising/sideways/falling R15 -0.000283/+0.211789/none; fresh134 R23 +0.000429; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Ethereum, Arbitrum and Optimism daily spot DEX volume from the public archive, pinned manifest `46de8bb0db959bb2af09d8631bb818820c88e292afa8d7c5a330615a3bcbd22a`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
