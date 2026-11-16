# LONGSCOUT-V65 results (research only): 0 of 2 pass

Frozen `a42d22d0` (rules) and `f05cfaa9` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l65_low_avwap_reclaim_4h | long | 251.5 | +0.010 | -0.011 | +0.049 | -0.040 | -0.015 | -0.022 | +0.109 | FAIL | late, ex-best, R23, fresh subset |
| s65_high_avwap_loss_4h | short | 187 | -0.042 | -0.064 | -0.171 | +0.136 | -0.068 | -0.049 | -0.041 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |

Mutation check: 5 of 5 planted mutants caught.

Ledger after this round: 228 frozen, 228 evaluated, 8 passes.
