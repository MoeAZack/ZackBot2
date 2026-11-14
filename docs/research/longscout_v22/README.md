# LONGSCOUT-V22 results (research only): 0 of 4 pass

Frozen `984cd36a` (rules) and `2dfdd214` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l22_vix_fix_spike_rising_4h | long | 371 | +0.021 | -0.001 | +0.072 | -0.054 | -0.001 | -0.031 | +0.119 | FAIL | late, ex-best, R23, fresh subset |
| r22_cci_regime_4h | both | 586 | -0.014 | -0.038 | +0.009 | -0.055 | -0.034 | -0.027 | +0.019 | FAIL | mean R15, late, ex-best, R23, fresh subset |
| l22_range_expansion_day_4h | long | 237.5 | -0.054 | -0.071 | -0.018 | -0.098 | -0.079 | -0.081 | -0.000 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s22_range_expansion_day_4h | short | 140 | -0.056 | -0.073 | -0.053 | -0.074 | -0.093 | -0.000 | -0.232 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Running total LONGSCOUT V1-V22: 107 candidates, 3 screen passes (r6 proceeding alone; s16 record only; r17 lead behind r6).
