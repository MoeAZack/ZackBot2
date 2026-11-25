# LONGSCOUT V205: 0/1 frozen screen passes

25 Nov 2026 15:51 Cairo. Source freeze `b23d916cfcd53e334fe60c2e9fea1e2392c77bcd`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 400 completed trials / 13 historical screen passes; 405 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l205_treasury_bill_primary_auction_demand_1d|2|-0.357618|-0.366839|none|1.014R Standard|FAIL|

Uses changes in accepted awards by bidder type in US bill primary auctions. V173 uses secondary sovereign real yields, V175 overnight repo dispersion, and V182 weekly Fed balance-sheet liquidity.

l205_treasury_bill_primary_auction_demand_1d: failures trades, meanR15, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.357618/none/none; long/short -0.357618/none; fallback-rule path trades (40 repeated orders) 1, order0 0.

l205_treasury_bill_primary_auction_demand_1d: pooled R23 -0.363418; fresh134 rising/sideways/falling R15 -0.366839/none/none; fresh134 R23 -0.372409; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: US Treasury 4-week bills competitive award composition from the public archive, pinned manifest `411714e35c7ef015623fced937be06374e8223b287c191e8d9fbbcf558fb459e`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
