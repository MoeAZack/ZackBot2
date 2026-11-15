# LONGSCOUT-V60 results (research only): 0 of 2 pass

Frozen `7e1b31ab` (rules) and `6d0c03a1` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s60_inside_week_break_4h | short | 105.5 | +0.003 | -0.024 | -0.027 | +0.032 | -0.022 | +0.048 | -0.130 | FAIL | early, ex-best, R23, extra subset |
| l60_inside_week_break_4h | long | 125 | -0.135 | -0.159 | -0.150 | -0.121 | -0.181 | -0.097 | -0.283 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Mutation check: 6 of 6 planted mutants caught.

This is the final LONGSCOUT round (moe, 15 Nov: "stop on 60"). Final ledger: 217 frozen, 217 evaluated, 8 screen passes
(r6, s16, r17, l24, s26, l40, s51, s54).
