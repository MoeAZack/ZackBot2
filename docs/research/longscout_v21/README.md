# LONGSCOUT-V21 results (research only): 0 of 4 pass

Frozen `eaf55dfb` (rules) and `61bfb418` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s21_btc_lead_catchdown_4h | short | 141 | +0.007 | -0.020 | +0.026 | +0.004 | -0.020 | -0.030 | +0.142 | FAIL | ex-best, R23, fresh subset |
| r21_autocorr_continuation_4h | both | 820.5 | -0.063 | -0.091 | -0.073 | -0.059 | -0.077 | -0.102 | +0.017 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
| l21_btc_lead_catchup_4h | long | 159 | -0.075 | -0.105 | -0.109 | -0.022 | -0.103 | -0.048 | -0.199 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l21_four_month_high_4h | long | 72 | -0.079 | -0.096 | +0.022 | -0.248 | -0.162 | +0.001 | -0.289 | FAIL | n<100, mean R15, late, ex-best, R23, order 0 30-per-half, extra subset |

Running total LONGSCOUT V1-V21: 103 candidates, 3 screen passes (r6 reproduced, proceeding alone; s16 record only; r17 lead behind r6).
