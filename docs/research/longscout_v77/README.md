# LONGSCOUT-V77 results (research only): 0 of 2 pass

Frozen `e64ba097` (rules) and `91b5b31c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s77_failed_bull_flag_4h | short | 134 | -0.017 | -0.035 | +0.063 | -0.081 | -0.044 | +0.040 | -0.136 | FAIL | mean R15, late, ex-best, R23, extra subset |
| s77_bear_flag_4h | short | 32 | -0.074 | -0.094 | +0.017 | -0.134 | -0.172 | -0.209 | +0.308 | FAIL | n<100, mean R15, late, ex-best, R23, order 0 30-per-half, fresh subset |

Notes: both rules fail. Tests: prefix, independent reference loops (with a removed-day gap), synthetic bear-flag and failed-flag edges,
regime gate and gap tests. 15 planted mutants: 14 caught (four after synthetic tests were added); dropping `c[j-1] >= low q` in the bear
flag is behaviour-equivalent, because a j-1 close below the pole low would also put a flag low below it.
