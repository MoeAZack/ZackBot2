# LONGSCOUT-V61 results (research only): 0 of 3 pass

Frozen `10ef635a` (rules) and `eae43ebb` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s61_bear_engulf_high_4h | short | 146.5 | -0.084 | -0.106 | +0.027 | -0.224 | -0.115 | -0.043 | -0.208 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| l61_bull_engulf_low_4h | long | 124.5 | -0.120 | -0.142 | -0.017 | -0.247 | -0.151 | -0.157 | -0.000 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s61_evening_star_4h | short | 72 | -0.169 | -0.192 | -0.130 | -0.245 | -0.212 | -0.164 | -0.201 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Mutation check: 9 of 9 planted mutants caught.

Ledger after this round: 220 frozen, 220 evaluated, 8 passes.
