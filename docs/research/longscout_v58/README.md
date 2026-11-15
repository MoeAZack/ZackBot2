# LONGSCOUT-V58 results (research only): 0 of 2 pass

Frozen `4ed601c0` (rules) and `e8b93b54` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l58_rsi_bull_divergence_4h | long | 103 | -0.008 | -0.030 | +0.189 | -0.180 | -0.051 | +0.065 | -0.183 | FAIL | mean R15, late, ex-best, R23, extra subset |
| s58_rsi_bear_divergence_4h | short | 151 | -0.071 | -0.088 | -0.107 | -0.001 | -0.099 | -0.189 | +0.172 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

Mutation check: 6 of 6 planted mutants caught.

Ledger after this round: 212 frozen, 212 evaluated, 8 passes.
