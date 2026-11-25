# LONGSCOUT V172: 0/1 frozen screen passes

25 Nov 2026 14:08 Cairo. Source freeze `3532725812c37c5636180bbd85039e858c75c7b9`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 368 completed trials / 13 historical screen passes; 373 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l172_btc_mining_pool_share_diffusion_1d|5|-0.100175|none|none|1.053R Standard|FAIL|

Tests dispersion of work across publicly attributed mining pools while aggregate hashrate stays near stable. V139 uses the aggregate amount of work, and V152 uses scheduled subsidy economics. Repairs the V169 input dtype with identical source bytes and trading rules, after V169 stopped before trades.

l172_btc_mining_pool_share_diffusion_1d: failures trades, meanR15, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.100175/none/none; long/short -0.100175/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l172_btc_mining_pool_share_diffusion_1d: pooled R23 -0.107099; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: top-three attributed BTC pool share and aggregate work from the public archive, pinned manifest `e34fff13d61d985b5fcc9532750e9d06c61c4ba6d5c37192cdba77e106314629`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
