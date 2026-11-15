# LONGSCOUT-V62 results (research only): 0 of 2 pass

Frozen `de431890` (rules) and `96bd1f87` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l62_selling_climax_4h | long | 25 | +0.276 | +0.263 | +0.160 | +0.397 | +0.205 | +0.468 | -0.070 | FAIL | n<100, order 0 30-per-half, extra subset |
| s62_buying_climax_4h | short | 121 | +0.004 | -0.010 | +0.062 | -0.081 | -0.021 | +0.035 | -0.125 | FAIL | late, ex-best, R23, extra subset |

l62 selling climax is positive (+0.276) but has only 25 closes and a negative extra31 subset; it fails n<100, order-0 count
and the extra gate. No gate relaxed.

Mutation check: 5 of 5 planted mutants caught.

Ledger after this round: 222 frozen, 222 evaluated, 8 passes.
