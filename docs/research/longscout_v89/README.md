# LONGSCOUT-V89 results (research only): 0 of 2 pass

Frozen `31874f3d` (rules) and `0815805d` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s89_broad_volume_downtrend_4h | short | 141 | -0.111 | -0.139 | -0.198 | -0.012 | -0.141 | -0.091 | -0.165 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l89_broad_volume_uptrend_4h | long | 72.5 | -0.118 | -0.141 | +0.013 | -0.654 | -0.179 | -0.014 | -0.290 | FAIL | n<100, mean R15, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Notes: both rules lose after costs (l89 also thin, median 72.5 closes). Ledger: 279 trials after V89, 11 passes. Tests: prefix,
independent pure-Python Herfindahl reference loop over several seeds (with a removed-day gap), HHI definition (equal days 1/30, one
dominant day near 1, dollar not raw volume), boundaries for the new 120-day low including day j-120 inside the window, R exactly 0 and
its base day j-30, missing day j-150 (monkeypatched H), regime gate with forced regimes, hour-zero. 12 planted mutants, all caught
(two after the day j-120 and day j-150 tests were added).
