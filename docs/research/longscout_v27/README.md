# LONGSCOUT-V27 results (research only): 0 of 3 pass

Frozen `cabdc2d0` (rules) and `36b79a7d` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r27_daily_staircase_4h | both | 309 | -0.059 | -0.082 | +0.010 | -0.169 | -0.084 | -0.066 | -0.021 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| r27_close_location_4h | both | 228 | -0.082 | -0.107 | -0.121 | -0.027 | -0.103 | -0.070 | -0.127 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l27_volume_dryup_near_high_4h | long | 29 | -0.309 | -0.339 | -0.257 | -0.637 | -0.512 | -0.257 | -1.047 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
