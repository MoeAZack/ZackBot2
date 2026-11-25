# LONGSCOUT V113: 0/1 frozen screen passes

25 Nov 2026 10:38 Cairo. Source freeze `3d2e3abed210e9fcc1a5a0f4872d67d07b3572a0`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 311 completed trials / 12 historical screen passes; 314 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r113_utc_slot_displacement_fade_4h|514|-0.078608|-0.044680|-0.153503|47.239R High|FAIL|

The range filter normalizes each completed 4h return against prior returns in the same UTC slot. V47 holds fixed sessions, V93 uses raw half-day displacement, and V106 learns the next day’s direction by weekday. None uses slot-specific robust dispersion to identify a range fade.

r113_utc_slot_displacement_fade_4h: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.077869/-0.085304/-0.073823; long/short -0.032366/-0.104311; fallback-rule path trades (40 repeated orders) 190, order0 4.

r113_utc_slot_displacement_fade_4h: pooled R23 -0.097067; fresh134 rising/sideways/falling R15 -0.042022/-0.030313/-0.067744; fresh134 R23 -0.062728; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, drawdown, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
