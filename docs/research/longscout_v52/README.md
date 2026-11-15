# LONGSCOUT-V52 results (research only): 0 of 3 pass

Frozen `b4bcb422` (rules) and `76c3da77` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s52_weekly_ema_touch_4h | short | 256 | +0.024 | -0.004 | -0.060 | +0.108 | +0.004 | +0.017 | +0.041 | FAIL | early, R23 |
| r52_daily_chop_trend_4h | both | 266 | -0.016 | -0.036 | -0.034 | -0.007 | -0.036 | +0.027 | -0.145 | FAIL | mean R15, early, late, ex-best, R23, extra subset |
| l52_weekly_ema_touch_4h | long | 264 | -0.153 | -0.182 | -0.029 | -0.337 | -0.175 | -0.141 | -0.181 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

s52 weekly EMA10 touch short is a near miss: positive mean and both subsets positive, but it fails the early half (-0.060) and R23 (-0.004). No gate relaxed.

Ledger after this round: 196 frozen, 196 evaluated, 7 passes.
