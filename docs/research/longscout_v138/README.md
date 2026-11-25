# LONGSCOUT V138: 0/1 frozen screen passes

25 Nov 2026 12:12 Cairo. Source freeze `294cc19f620a0aadf31191f2c51f0fab2b1cd7aa`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 336 completed trials / 12 historical screen passes; 339 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l138_btc_blockspace_demand_pulse_1d|26|-0.169650|-0.175987|none|6.273R Standard|FAIL|

Observes fees paid to confirm Bitcoin transactions and transaction count. Earlier participation families use orders or turnover on exchanges; V117/V123/V135 use perpetual funding, which settles between traders.

l138_btc_blockspace_demand_pulse_1d: failures trades, meanR15, early, late, ex_best_coin, meanR23, order0_30_per_half, fresh134, extra31; BTC rising/sideways/falling R15 -0.094644/-0.299385/none; long/short -0.169650/none; fallback-rule path trades (40 repeated orders) 8, order0 0.

l138_btc_blockspace_demand_pulse_1d: pooled R23 -0.178688; fresh134 rising/sideways/falling R15 -0.148389/none/none; fresh134 R23 -0.183704; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Entry feature: BTC FeeTotNtv and TxCnt after two full UTC days from the public archive, pinned manifest `6ad785fa29e62584eba63ea351c6f43596fb9de95368e0f17bed615f55efd7a8`. Missing bars are masked.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
