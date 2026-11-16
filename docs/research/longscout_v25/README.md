# LONGSCOUT-V25 results (research only): 0 of 1 valid pass (3 rows invalid, see below)

Frozen `f1fc8dde` (rules) and `453badec` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l25_turn_of_month_4h | long | 46 | -0.011 | -0.032 | +0.027 | -0.022 | -0.127 | +0.007 | +0.033 | FAIL | n<100, mean R15, late, ex-best, R23, order 0 30-per-half |
| s25_weekly_close_breakdown_4h | short | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
| r25_daily_heikin_flip_4h | both | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
| l25_weekly_close_breakout_4h | long | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

## Implementation defect: three rules opened zero trades (not a strategy result)

`l25_weekly_close_breakout_4h`, `s25_weekly_close_breakdown_4h` and `r25_daily_heikin_flip_4h` opened **0 trades on all
165 coins**. Cause: `_day_is_yesterday` compared raw int64 views of two timestamps with different units (the real loader
gives `datetime64[ms]`; the shifted row date was not ms), so the "previous day" check was never true. The synthetic tests
used ns timestamps and missed it. No trade return of these three rules was ever produced, so nothing about their
performance has been seen.

These three rows are **INVALID_NO_TRADES (implementation defect)**, not evidence against the ideas. The same hypotheses,
parameters and management are re-frozen with only the comparison fixed, plus ms/s timestamp tests, as LONGSCOUT-V26
(`l26_*`, `s26_*`, `r26_*`). `l25_turn_of_month_4h` did not use the helper; its FAIL stands.
