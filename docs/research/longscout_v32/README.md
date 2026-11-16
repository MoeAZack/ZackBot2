# LONGSCOUT-V32 results (research only): 0 of 3 pass

Frozen `55f1b6ac` (rules) and `a7c135d7` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l32_updown_volume_4h | long | 225.5 | +0.017 | -0.001 | +0.047 | -0.025 | -0.006 | -0.016 | +0.176 | FAIL | late, ex-best, R23, fresh subset |
| r32_vwap30_cross_4h | both | 410 | +0.017 | -0.007 | +0.005 | +0.047 | -0.026 | -0.064 | +0.239 | FAIL | ex-best, R23, fresh subset |
| r32_efficiency_ratio_4h | both | 203 | -0.018 | -0.039 | -0.019 | -0.035 | -0.048 | -0.041 | +0.050 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
