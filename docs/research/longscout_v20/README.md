# LONGSCOUT-V20 results (research only): 0 of 4 pass

Frozen `fea01235` (rules) and `f41f30c5` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s20_relative_pop_in_downtrend_4h | short | 261.5 | -0.024 | -0.044 | -0.123 | +0.062 | -0.045 | +0.008 | -0.109 | FAIL | mean R15, early, ex-best, R23, extra subset |
| s20_quiet_volume_breakdown_4h | short | 108 | -0.036 | -0.060 | +0.004 | -0.089 | -0.088 | -0.055 | +0.066 | FAIL | mean R15, late, ex-best, R23, fresh subset |
| l20_relative_dip_in_uptrend_4h | long | 305 | -0.139 | -0.157 | -0.054 | -0.276 | -0.158 | -0.111 | -0.264 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l20_quiet_volume_breakout_4h | long | 167 | -0.175 | -0.195 | -0.100 | -0.280 | -0.208 | -0.183 | -0.092 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Running total LONGSCOUT V1-V20: 99 candidates, 3 screen passes (r6 reproduced by Codex; s16 weak; r17 same family as r6).
