# LONGSCOUT-V37 results (research only): 0 of 3 pass

Frozen `5e99bcaa` (rules) and `e014f917` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l37_daily_hammer_low_4h | long | 59 | +0.088 | +0.067 | +0.217 | -0.132 | +0.028 | +0.035 | +0.239 | FAIL | n<100, late, order 0 30-per-half |
| s37_pivot_s1_break_4h | short | 498.5 | -0.033 | -0.065 | -0.076 | +0.012 | -0.049 | -0.044 | +0.006 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| l37_pivot_r1_break_4h | long | 614.5 | -0.055 | -0.086 | -0.018 | -0.107 | -0.082 | -0.041 | -0.078 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
