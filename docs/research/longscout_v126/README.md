# LONGSCOUT V126: 0/1 frozen screen passes

25 Nov 2026 11:29 Cairo. Source freeze `5ebdf6bc794c75e1e4c89db26ce1d6341a9624cf`; immutable cases preregistered before outcomes. See the case manifest for remote/local publication timing. Ledger: 324 completed trials / 12 historical screen passes; 327 registered.

| Candidate | Median closes | MeanR15 | fresh134 | extra31 | Median closedDD | Result |
|---|---:|---:|---:|---:|---|---|
|r126_competing_first_passage_fade_1d|168|-0.079347|-0.050562|-0.163897|15.598R Elevated|FAIL|

Learns which of two competing price barriers is reached first after a completed move. V105 and V110 learn average next-day returns. V21 and V81 measure serial dependence. None conditions on the sequence of barrier crossings.

r126_competing_first_passage_fade_1d: failures meanR15, early, late, ex_best_coin, meanR23, fresh134, extra31; BTC rising/sideways/falling R15 -0.072715/-0.088629/none; long/short -0.044126/-0.103533; fallback-rule path trades (40 repeated orders) 48, order0 1.

r126_competing_first_passage_fade_1d: pooled R23 -0.087702; fresh134 rising/sideways/falling R15 -0.035464/-0.083783/none; fresh134 R23 -0.058598; fresh134 top-three/net undefined; forward bar FAIL (wide_screen, rising, sideways, stress, top3).

Require WIDE165_PASS, positive fresh134 R15 in rising and sideways BTC regimes, fresh134 median R23 >= 0, Standard/Elevated pooled median closed DD, and fresh134 median top-three winning trades / net profit < 50%. Use path trades and all 40 orders, with missing denominators failing the bar.

Research-only/defaultOFF. 165 coins,40orders,unchanged seven rule-v1 gates and BOTH subset gates. Primary15bp fees PLUS2bp slip/side; stress23bp fees PLUS2bp slip. Native funding fixed .00005/4h of held notional, both sides pay; not realized historical funding. Frozen testnet snapshot is not a historical rules series; ICPUSDT/TLMUSDT lack snapshot rules and use fallback. Shared calendar already consumed, not untouched holdout. No tuning, coin dropping or subgroup rescue.

DD uses closed equity with initialzero, notMTM. Standard<=15R, Elevated<=30R, High<=60R, Extreme>60R. Exact tests in tests.log; source/case SHA and both path/worst tapes preserved. Saved-tape seven-gate/subset reconstruction matches; not independent market-price replay or profitability acceptance.

The frozen economic rules are rejected. Search proceeds to the next preregistered version.

ROADMAP FIT: new long/range economics under the continuing research-only LONGSCOUT contract. DEFERRED: accounts/runtime/scheduler/LEADFWD/PAPER/mainnet/merge/mark-ready.
