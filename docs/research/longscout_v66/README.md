# LONGSCOUT-V66 results (research only): 0 of 3 pass

Frozen `1ac7ccf8` (rules) and `3103b7ec` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s66_pocket_pivot_short_4h | short | 222 | -0.009 | -0.032 | -0.014 | -0.002 | -0.035 | -0.029 | +0.079 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
| r66_ma_compression_break_4h | both | 288 | -0.060 | -0.089 | -0.016 | -0.139 | -0.080 | -0.062 | -0.076 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l66_pocket_pivot_4h | long | 275 | -0.084 | -0.104 | -0.096 | -0.082 | -0.119 | -0.096 | -0.041 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Mutation check: 7 of 7 planted mutants caught.

Ledger after this round: 231 frozen, 231 evaluated, 8 passes.
