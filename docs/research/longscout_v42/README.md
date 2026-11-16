# LONGSCOUT-V42 results (research only): 0 of 3 pass

Frozen `be5d92b5` (rules) and `b289e34d` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l42_strength_on_btc_down_day_4h | long | 79 | +0.072 | +0.059 | +0.075 | +0.094 | +0.012 | +0.054 | +0.115 | FAIL | n<100 |
| s42_weakness_on_btc_up_day_4h | short | 36 | -0.025 | -0.037 | -0.095 | +0.023 | -0.090 | -0.001 | -0.171 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
| r42_daily_mfi_extreme_4h | both | 137 | -0.060 | -0.080 | -0.125 | +0.067 | -0.084 | -0.054 | -0.080 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |

`l42_strength_on_btc_down_day_4h` (+0.072, every return gate positive) fails only the trade count (79 < 100). It is
recorded as FAIL, and the gate is not relaxed. This is the third count-only near miss in this resumed batch (with s36 and
s40), all event rules with too few events on this calendar.
