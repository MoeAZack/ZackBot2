# LONGSCOUT-V23 results (research only): 0 of 4 pass

Frozen `5fd21210` (rules) and `02f374ae` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s23_sma100_touch_falling_4h | short | 240.5 | -0.027 | -0.051 | -0.155 | +0.124 | -0.046 | +0.017 | -0.122 | FAIL | mean R15, early, ex-best, R23, extra subset |
| l23_daily_rsi_reset_rising_4h | long | 163 | -0.089 | -0.113 | +0.001 | -0.219 | -0.130 | -0.153 | +0.099 | FAIL | mean R15, late, ex-best, R23, fresh subset |
| s23_daily_rsi_reset_falling_4h | short | 134.5 | -0.118 | -0.141 | -0.104 | -0.131 | -0.148 | -0.170 | +0.003 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
| l23_sma100_touch_rising_4h | long | 248 | -0.143 | -0.164 | -0.039 | -0.283 | -0.164 | -0.178 | -0.053 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Running total LONGSCOUT V1-V23: 111 candidates, 3 screen passes (r6 proceeding alone; s16 record only; r17 lead behind r6).
