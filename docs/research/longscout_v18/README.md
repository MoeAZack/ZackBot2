# LONGSCOUT-V18 results (research only): 0 of 4 pass

Frozen `6843ada3` (rules) and `ca76fa03` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s18_monday_drift_short_4h | short | 188 | -0.006 | -0.035 | -0.045 | +0.029 | -0.045 | +0.007 | -0.082 | FAIL | mean R15, early, ex-best, R23, extra subset |
| s18_high_vol_falling_short_4h | short | 192 | -0.042 | -0.057 | -0.090 | +0.004 | -0.071 | +0.017 | -0.222 | FAIL | mean R15, early, ex-best, R23, extra subset |
| l18_monday_drift_long_4h | long | 196 | -0.069 | -0.094 | -0.032 | -0.099 | -0.092 | -0.086 | -0.032 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l18_high_vol_rising_long_4h | long | 165 | -0.103 | -0.117 | -0.053 | -0.184 | -0.137 | -0.123 | -0.055 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Running total LONGSCOUT V1-V18: 91 candidates, 3 screen passes (r6 reproduced by Codex; s16 weak; r17 same family as r6).
