# LONGSCOUT-V13 results (research only): 0 of 4 pass

Frozen `08117bef` (rules) and `ab645b6c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l13_daily_higher_lows_4h | long | 259 | -0.001 | -0.025 | +0.066 | -0.098 | -0.027 | -0.051 | +0.156 | FAIL | mean R15, late, ex-best, R23, fresh subset |
| l13_strong_close_streak_4h | long | 310.5 | -0.045 | -0.078 | -0.036 | -0.056 | -0.103 | -0.066 | +0.041 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
| s13_daily_lower_highs_4h | short | 212 | -0.045 | -0.070 | -0.025 | -0.085 | -0.076 | -0.080 | +0.052 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
| r13_rsi50_regime_4h | both | 953.5 | -0.058 | -0.083 | -0.034 | -0.082 | -0.070 | -0.059 | -0.040 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Running total LONGSCOUT V1-V13: 70 candidates, 1 screen pass (r6_regime_flip_4h, independently reproduced by Codex).
