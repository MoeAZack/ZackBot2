# LONGSCOUT V119: 0/1 frozen screen passes

25 Nov 2026 11:03 Cairo. Source freeze `39735b577995a348666cc78861e8e0f787f09857`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 317 completed trials / 12 historical screen passes; 320 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r119_btc_position_concentration_fade_1d|57|-0.043308|+0.033997|-0.306605|6.872R Standard|FAIL|

Compares position concentration by direction with account counts and fades when price moves against the concentrated side. V117 uses settled funding and V118 uses total open interest. Neither observes how exposure is distributed by side.

r119_btc_position_concentration_fade_1d: failures trades, meanR15, early, ex_best_coin, meanR23, order0_30_per_half, extra31; BTC rising/sideways/falling R15 -0.066907/+0.009909/none; long/short -0.037854/-0.035199; fallback-rule path trades (40 repeated orders) 33, order0 1.

r119_btc_position_concentration_fade_1d: pooled R23 -0.052551; fresh134 rising/sideways/falling R15 +0.005452/+0.071379/none; fresh134 R23 +0.026086; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC top-trader account and position ratios from the public Metrics archive, pinned manifest `f1bb1094b5ea58a1843f7f02037e98a20434d816266275f1848e5d8042d1918f`. Missing dates are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
