# LONGSCOUT-V46 results (research only): 0 of 3 pass

Frozen `f7c4ac42` (rules) and `b6d69abd` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l46_falling_oversold_bounce_4h | long | 26 | +0.295 | +0.278 | +0.519 | +0.038 | +0.235 | +0.361 | +0.103 | FAIL | n<100, order 0 30-per-half |
| l46_falling_crash_bounce_4h | long | 33 | +0.033 | +0.025 | -0.004 | +0.052 | +0.000 | +0.022 | +0.099 | FAIL | n<100, early, order 0 30-per-half |
| s46_rising_overbought_fade_4h | short | 59 | -0.004 | -0.015 | -0.020 | +0.047 | -0.038 | +0.000 | -0.017 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, extra subset |

`l46_falling_oversold_bounce_4h` is +0.295 R15 with every return gate positive, but has only 26 closes (a deeply
oversold daily RSI inside a falling regime is rare). It is FAIL on the count gates, and no gate is relaxed. Such a small
sample cannot separate an edge from luck, so it is only a candidate for forward collection on new data.
