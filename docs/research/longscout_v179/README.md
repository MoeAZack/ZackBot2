# LONGSCOUT V179: 0/1 frozen screen passes

25 Nov 2026 14:25 Cairo. Source freeze `f59a19eb279c42c40b2926e505461d4c822bb2ca`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 375 completed trials / 13 historical screen passes; 380 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l179_aave_stablecoin_debt_expansion_1d|3|-0.708463|none|none|2.125R Standard|FAIL|

Tests outstanding stablecoin loan demand at comparable lending-pool liquidity. V140 measures token supply regardless of use, V146 studies futures contracts versus trading turnover and V175 uses traditional repo rates.

l179_aave_stablecoin_debt_expansion_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.708463/none/none; long/short -0.708463/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l179_aave_stablecoin_debt_expansion_1d: pooled R23 -0.716540; fresh134 rising/sideways/falling R15 none/none/none; fresh134 R23 none; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: Ethereum Aave V2+V3 raw USDC and DAI cash balances and debt from the public archive, pinned manifest `e0f10c764869157ab9c3d92d57ecdba327d904c309ee863e99b36f12528abf51`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
