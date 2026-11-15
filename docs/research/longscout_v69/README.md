# LONGSCOUT-V69 results (research only): 0 of 2 pass

Frozen `3ce68ee3` (rules) and `4fe3fce9` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s69_dead_cat_fail_4h | short | 145 | -0.062 | -0.083 | -0.076 | -0.051 | -0.091 | -0.076 | -0.012 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l69_morning_star_4h | long | 51 | -0.077 | -0.099 | -0.164 | +0.015 | -0.138 | -0.080 | -0.061 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Notes: both rules lose after costs. Tests: prefix, independent reference loops, synthetic morning-star and dead-cat edges, regime gate and
gap tests; 13 planted mutants all caught (the doji mutant after a synthetic test was added).
