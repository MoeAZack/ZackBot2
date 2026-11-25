# LONGSCOUT V183: 0/1 frozen screen passes

25 Nov 2026 14:40 Cairo. Source freeze `cebcffbf1b18257d451d89b8f15b863898923a65`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 379 completed trials / 13 historical screen passes; 384 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l183_btc_header_gap_tail_normalizes_1d|27|-0.070584|-0.071295|none|3.820R Standard|FAIL|

Uses the distribution of nominal block gaps at comparable daily block count. V139 tracks estimated total computational work, V169/V172 mining pool share, and V165 the unconfirmed transaction queue.

l183_btc_header_gap_tail_normalizes_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.091976/-0.022905/none; long/short -0.070584/none; fallback-rule path trades (40 repeated orders) 3, order0 0.

l183_btc_header_gap_tail_normalizes_1d: pooled R23 -0.078906; fresh134 rising/sideways/falling R15 -0.082798/-0.047767/none; fresh134 R23 -0.079302; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: daily canonical Bitcoin block count and 30-minute header gaps from the public archive, pinned manifest `68ebe16ad29c0df148d581cdd2b02a5a1446f776f3710d26f449d45fa1ab9b47`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
