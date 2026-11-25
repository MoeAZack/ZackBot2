# LONGSCOUT V105: 0/2 frozen screen passes

25 Nov 2026 10:10 Cairo. Source freeze `f2c54ac54fe43cfaa62d4c9f753b360d61dda832`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 303 completed trials / 11 historical screen passes; 305 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l105_downside_bipower_discontinuity_4h|217|-0.054688|-0.046666|-0.040399|24.570R Elevated|FAIL|
|r105_volume_conditional_reversal_4h|213|-0.057740|-0.005782|-0.213437|19.709R Elevated|FAIL|

The long tests downside RV minus bipower variation, distinct from V84 signed semivariance and V91 range-volatility normalization. The range arm learns next-day reversal by high-volume return sign, distinct from V21 autocorrelation and V95 lagged volume/return correlation.

l105_downside_bipower_discontinuity_4h: failures meanR15, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.048250/+0.028733/-0.121789; long/short -0.054688/none; fallback-rule path trades (40 repeated orders) 82, order0 0.

l105_downside_bipower_discontinuity_4h: pooled R23 -0.071803; fresh134 rising/sideways/falling R15 -0.017784/-0.160565/-0.105459; fresh134 R23 -0.063394; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

r105_volume_conditional_reversal_4h: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.004388/-0.270027/+0.020951; long/short +0.053229/-0.181282; fallback-rule path trades (40 repeated orders) 77, order0 1.

r105_volume_conditional_reversal_4h: pooled R23 -0.077403; fresh134 rising/sideways/falling R15 +0.004120/-0.182285/+0.264536; fresh134 R23 -0.025023; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
