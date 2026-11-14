# LONGSCOUT-V19 results (research only): 0 of 4 pass

Frozen `25fbc3bb` (rules) and `21a4d81c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s19_green_days_in_downtrend_4h | short | 244 | +0.027 | -0.001 | +0.029 | +0.008 | +0.004 | +0.048 | -0.041 | FAIL | R23, extra subset |
| s19_shooting_star_downtrend_4h | short | 289 | +0.010 | -0.028 | -0.017 | +0.059 | -0.014 | +0.090 | -0.190 | FAIL | early, ex-best, R23, extra subset |
| l19_hammer_in_uptrend_4h | long | 198 | -0.003 | -0.038 | +0.065 | -0.207 | -0.042 | +0.030 | -0.172 | FAIL | mean R15, late, ex-best, R23, extra subset |
| l19_red_days_in_uptrend_4h | long | 283 | -0.164 | -0.183 | -0.088 | -0.248 | -0.181 | -0.130 | -0.217 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Running total LONGSCOUT V1-V19: 95 candidates, 3 screen passes (r6 reproduced by Codex; s16 weak; r17 same family as r6).
