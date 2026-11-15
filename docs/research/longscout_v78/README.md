# LONGSCOUT-V78 results (research only): 0 of 2 pass

Frozen `2e8826e3` (rules) and `4f26bcf5` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l78_cup_handle_4h | long | 30 | -0.066 | -0.095 | -0.379 | +0.964 | -0.143 | -0.027 | -0.157 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
| s78_inverted_cup_4h | short | 57 | -0.071 | -0.100 | +0.092 | -0.239 | -0.117 | +0.031 | -0.218 | FAIL | n<100, mean R15, late, ex-best, R23, order 0 30-per-half, extra subset |

Notes: both rules are thin and negative. Tests: prefix, independent reference loop (with a removed-day gap), synthetic cup edges (lip,
depth, handle bounds, window ends, tied right lip, mirror), regime gate including forced-regime checks for the excluded side, and gap
test; 14 planted mutants all caught (the short lip mutant after a synthetic test was added).
