# LONGSCOUT-V12 results (research only): 0 of 4 pass

Frozen `02597556` (rules) and `1c32d880` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s12_prior_week_low_break_4h | short | 174.5 | -0.007 | -0.031 | -0.047 | +0.035 | -0.051 | -0.013 | +0.016 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| s12_weak_week_continuation_4h | short | 66 | -0.014 | -0.026 | -0.101 | +0.034 | -0.046 | -0.099 | +0.397 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset |
| l12_prior_week_high_break_4h | long | 236 | -0.027 | -0.051 | -0.064 | +0.039 | -0.099 | -0.027 | -0.050 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |
| l12_strong_week_continuation_4h | long | 148 | -0.074 | -0.085 | +0.041 | -0.247 | -0.141 | -0.095 | +0.015 | FAIL | mean R15, late, ex-best, R23, fresh subset |

Running total LONGSCOUT V1-V12: 66 candidates, 1 screen pass (r6_regime_flip_4h independently reproduced by Codex).
