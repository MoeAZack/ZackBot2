# LONGSCOUT V121: 0/1 frozen screen passes

25 Nov 2026 11:11 Cairo. Source freeze `c9c4bfb4aec3af5d0e6e92016c34b04d4ea50bb0`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 319 completed trials / 12 historical screen passes; 322 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l121_relative_stablecoin_normalization_4h|12|+0.314020|none|none|1.517R Standard|FAIL|

Uses normalization of relative stablecoin pricing as a market-wide quote liquidity input. V117 uses contract funding and V120 compares futures premium with BTC price. Earlier BTC recovery rules do not observe a stablecoin pair.

l121_relative_stablecoin_normalization_4h: failures trades, late, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 +0.091716/+1.387982/none; long/short +0.314020/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l121_relative_stablecoin_normalization_4h: pooled R23 +0.297931; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: USDCUSDT spot 4h close from the public archive, pinned manifest `e4a4e13834bf1d19dd5f39a6c61fc05201a063f57c78dc4e4764d3102c7ae637`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
