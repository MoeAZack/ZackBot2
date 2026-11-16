# LONGSCOUT-V56 results (research only): 0 of 3 pass

Frozen `8adc75ba` (rules) and `1c17d82c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s56_daily_upthrust_4h | short | 177 | +0.010 | -0.010 | +0.020 | -0.008 | -0.013 | +0.049 | -0.057 | FAIL | late, ex-best, R23, extra subset |
| r56_key_reversal_4h | both | 170 | -0.018 | -0.038 | -0.073 | +0.060 | -0.041 | -0.030 | -0.002 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |
| l56_daily_spring_4h | long | 141 | -0.136 | -0.158 | -0.117 | -0.158 | -0.169 | -0.095 | -0.238 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Mutation check: 7 of 7 planted mutants caught.

Ledger after this round: 207 frozen, 207 evaluated, 8 passes.
