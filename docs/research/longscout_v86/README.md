# LONGSCOUT-V86 results (research only): 0 of 2 pass

Frozen `39885942` (rules) and `821181b9` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s86_smooth_downtrend_4h | short | 85 | +0.171 | +0.147 | +0.097 | +0.211 | +0.125 | +0.129 | +0.273 | FAIL | n<100 |
| l86_smooth_uptrend_4h | long | 56 | -0.023 | -0.044 | -0.097 | +0.184 | -0.073 | +0.012 | -0.222 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, extra subset |

Notes: l86 fails most gates. s86 is positive on every return gate (mean R15 +0.171, R23 +0.147, both halves, ex-best, fresh134
+0.129, extra31 +0.273) but FAILS the frozen sample-size gate: median 85 closes < 100. It stays FAIL; no parameter is loosened and no
rerun is made to raise the count. It is logged as a thin-sample near-miss only. Ledger: 273 trials after V86, 11 passes. Tests: prefix,
independent pure-Python ulcer-index reference loop over several seeds (with a removed-day gap), synthetic choppy-then-smooth series
(exact first new-low day, repeat low not first), missing day j-133, close below the 14-day high, boundaries for the new low, the 14-day
extreme tie, the 30-day trend and its base day j-30 (monkeypatched index), regime gate with forced regimes, hour-zero. 14 planted
mutants, all caught (both base-day shift mutants after the base-day test was added).
