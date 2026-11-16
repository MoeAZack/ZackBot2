# LONGSCOUT-V33 results (research only): 0 of 3 pass

Frozen `6d3bbb3a` (rules) and `056b1c9a` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s33_churn_exhaustion_4h | short | 108 | -0.033 | -0.041 | -0.062 | +0.010 | -0.063 | +0.001 | -0.240 | FAIL | mean R15, early, ex-best, R23, extra subset |
| l33_btc_decoupling_4h | long | 140 | -0.100 | -0.120 | +0.006 | -0.263 | -0.131 | -0.143 | +0.051 | FAIL | mean R15, late, ex-best, R23, fresh subset |
| l33_high_beta_btc_up_4h | long | 17 | -0.116 | -0.133 | +0.139 | -0.573 | -0.420 | -0.228 | n/a | FAIL | n<100, mean R15, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
