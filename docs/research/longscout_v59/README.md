# LONGSCOUT-V59 results (research only): 0 of 3 pass

Frozen `d13b430d` (rules) and `f16bb6a4` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s59_range_low_persist_4h | short | 125 | -0.064 | -0.091 | -0.243 | +0.117 | -0.096 | -0.137 | +0.100 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| l59_range_top_persist_4h | long | 82.5 | -0.095 | -0.118 | -0.097 | -0.040 | -0.146 | +0.028 | -0.398 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, extra subset |
| r59_weekly_engulfing_4h | both | 170 | -0.151 | -0.177 | -0.078 | -0.272 | -0.177 | -0.124 | -0.211 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Mutation check: 8 of 8 planted mutants caught (three needed a synthetic weekly test, added before freeze).

Ledger after this round: 215 frozen, 215 evaluated, 8 passes.
