# LONGSCOUT-V55 results (research only): 0 of 3 pass

Frozen `8a9aeee7` (rules) and `28c8731b` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s55_head_shoulders_4h | short | 54 | +0.185 | +0.162 | +0.194 | +0.195 | +0.119 | +0.233 | +0.029 | FAIL | n<100, order 0 30-per-half |
| l55_inverse_head_shoulders_4h | long | 41 | +0.060 | +0.037 | +0.050 | +0.043 | -0.035 | +0.379 | -0.482 | FAIL | n<100, ex-best, order 0 30-per-half, extra subset |
| l55_bull_flag_4h | long | 36 | -0.175 | -0.192 | -0.260 | +0.171 | -0.237 | -0.337 | +0.703 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset |

s55 head-and-shoulders short is a near miss: every return gate is positive (+0.185, both halves, ex-best, both subsets), but
it has only 54 closes (<100) and fails the order-0 30-per-half count. No gate relaxed. It points the same way as V54's
s54 double top (bearish reversal patterns), which is worth knowing but is not independent evidence: same family, same
calendar.

Mutation check: 10 of 11 planted mutants caught. Dropping `c[j-1] <= h[q]` from the bull flag is behaviour-equivalent,
because the flag condition already keeps every high of days q+1..j-1 at or below h[q].

Ledger after this round: 204 frozen, 204 evaluated, 8 passes.
