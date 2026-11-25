# LONGSCOUT V111: 0/1 frozen screen passes

25 Nov 2026 10:31 Cairo. Source freeze `b67bc7fd38aa0292216838fc085b9c6f7ce725de`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 309 completed trials / 12 historical screen passes; 312 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l111_point_figure_higher_base_1d|110|-0.021905|-0.012760|-0.042353|11.082R Standard|FAIL|

The signal compares alternating point-and-figure columns, which are defined by three-box reversals on quantized prices. V2/V4 use calendar Donchian levels and V74 uses time-bar fractals.

l111_point_figure_higher_base_1d: failures meanR15, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.041023/-0.102823/+0.081493; long/short -0.021905/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l111_point_figure_higher_base_1d: pooled R23 -0.030083; fresh134 rising/sideways/falling R15 -0.056670/-0.290976/+0.297920; fresh134 R23 -0.020700; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
