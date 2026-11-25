# LONGSCOUT V201: 0/1 frozen screen passes

25 Nov 2026 15:41 Cairo. Source freeze `066ce7d1b0d96c5e7f0c36e120956cd2ca2014fb`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 396 completed trials / 13 historical screen passes; 401 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l201_uniswap_eth_swap_fee_relief_1d|2|-0.713634|none|none|1.427R Standard|FAIL|

Tests actual fees per traded dollar in a fixed on-chain exchange and chain. V178 measures the allocation of dollar volume and V180 estimates a futures spread from return covariance, without observing paid swap fees.

l201_uniswap_eth_swap_fee_relief_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.713634/none/none; long/short -0.713634/none; fallback-rule path trades (40 repeated orders) 0, order0 0.

l201_uniswap_eth_swap_fee_relief_1d: pooled R23 -0.722357; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Uniswap V3 Ethereum user swap fees and traded volume from the public archive, pinned manifest `a668b58c0935482641593241984d29366fecbabedab14f73455c17352c0f8a13`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
