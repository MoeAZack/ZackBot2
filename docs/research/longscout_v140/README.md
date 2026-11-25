# LONGSCOUT V140: 0/1 frozen screen passes

25 Nov 2026 12:20 Cairo. Source freeze `bf490eafb65df145cf14417677d1a6a1c5716224`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 338 completed trials / 12 historical screen passes; 341 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l140_eth_stablecoin_joint_expansion_1d|13|-0.019574|-0.128683|none|1.700R Standard|FAIL|

Tests the outstanding nominal quantity of two dollar tokens on one fixed chain. V121 uses a spot peg ratio and V132 uses arbitrage residuals. Neither observes issuance and redemption reflected in token supply.

l140_eth_stablecoin_joint_expansion_1d: failures trades, meanR15, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.101382/+0.342046/none; long/short -0.019574/none; fallback-rule path trades (40 repeated orders) 5, order0 0.

l140_eth_stablecoin_joint_expansion_1d: pooled R23 -0.027191; fresh134 rising/sideways/falling R15 -0.155523/+0.211789/none; fresh134 R23 -0.135412; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: USDC and USDT Ethereum supply plus completion time from the public archive, pinned manifest `ce18b22424e0b2352bc4a46ad6219c52cacecee09a53e166b22e032ef8dfa098`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
