# LONGSCOUT V106: 1/2 frozen screen passes

25 Nov 2026 10:14 Cairo. Source freeze `11b2f763b8b970bcf704fd56adb3752725a8367a`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 305 completed trials / 12 historical screen passes; 307 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|l106_learned_weekday_1d|199.5|+0.071161|+0.071664|+0.064302|8.396R Standard|WIDE165_PASS|
|r106_daily_vwap_displacement_1d|234|-0.034839|-0.024995|-0.066819|14.480R Standard|FAIL|

The long learns which weekdays historically worked for each coin, distinct from the fixed Monday and weekend holds in V18/V31. The range arm fades settlement against the same-day transacted price, distinct from rolling VWAP trend crosses in V11/V32 and swing-anchored trend reclaims in V5/V65.

l106_learned_weekday_1d: failures none; BTC rising/sideways/falling R15 +0.040253/+0.055627/+0.128954; long/short +0.071161/none; fallback-rule path trades (40 repeated orders) 59, order0 1.

l106_learned_weekday_1d: pooled R23 +0.057811; fresh134 rising/sideways/falling R15 +0.061541/+0.012777/+0.149288; fresh134 R23 +0.057928; fresh134 top-three/net 51.70%; forward bar FAIL (top3).

r106_daily_vwap_displacement_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 +0.005319/-0.076871/-0.075150; long/short -0.039331/-0.036943; fallback-rule path trades (40 repeated orders) 185, order0 4.

r106_daily_vwap_displacement_1d: pooled R23 -0.043893; fresh134 rising/sideways/falling R15 +0.043404/-0.096535/-0.090889; fresh134 R23 -0.034222; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

A screen pass is a lead for independent reproduction. No book is activated.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
