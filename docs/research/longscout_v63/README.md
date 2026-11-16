# LONGSCOUT-V63 results (research only): 0 of 2 pass

Frozen `ee227c53` (rules) and `f95385f5` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s63_vcp_breakdown_4h | short | 38 | +0.208 | +0.184 | +0.132 | +0.292 | +0.134 | +0.076 | +0.634 | FAIL | n<100, order 0 30-per-half |
| l63_vcp_breakout_4h | long | 25 | -0.124 | -0.151 | -0.310 | +0.620 | -0.211 | -0.194 | -0.083 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

s63 VCP breakdown short is positive on every return gate (+0.208) but has only 38 closes; it fails n<100 and the order-0
count. No gate relaxed.

Mutation check: 6 of 6 planted mutants caught.

Ledger after this round: 224 frozen, 224 evaluated, 8 passes.
