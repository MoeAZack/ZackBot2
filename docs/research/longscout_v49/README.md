# LONGSCOUT-V49 results (research only): 0 of 3 pass

Frozen `a523e6cc` (rules) and `4fae85f4` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s49_daily_stoch_down_4h | short | 162 | -0.034 | -0.064 | -0.085 | +0.042 | -0.067 | -0.045 | -0.020 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |
| r49_daily_trix_4h | both | 188.5 | -0.180 | -0.204 | -0.172 | -0.193 | -0.208 | -0.165 | -0.222 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l49_daily_stoch_up_4h | long | 186 | -0.198 | -0.226 | -0.183 | -0.231 | -0.223 | -0.277 | +0.008 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

Ledger after this round: 187 frozen, 187 evaluated, 6 passes.
