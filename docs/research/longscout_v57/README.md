# LONGSCOUT-V57 results (research only): 0 of 3 pass

Frozen `2ba149ba` (rules) and `d0284358` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l57_falling_wedge_4h | long | 218 | -0.020 | -0.043 | +0.035 | -0.107 | -0.043 | -0.047 | +0.110 | FAIL | mean R15, late, ex-best, R23, fresh subset |
| s57_rising_wedge_4h | short | 152 | -0.070 | -0.098 | +0.013 | -0.180 | -0.096 | -0.029 | -0.204 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| r57_triangle_break_4h | both | 157 | -0.188 | -0.216 | -0.287 | -0.049 | -0.217 | -0.218 | -0.088 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Mutation check: 7 of 7 planted mutants caught.

Ledger after this round: 210 frozen, 210 evaluated, 8 passes.
