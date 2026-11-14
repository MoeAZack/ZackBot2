# LONGSCOUT-V29 results (research only): 0 of 3 pass

Frozen `4b5f7e4e` (rules) and `9db41471` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r29_daily_squeeze_release_4h | both | 235 | +0.067 | +0.044 | +0.097 | +0.044 | +0.023 | +0.098 | -0.017 | FAIL | extra subset |
| s29_ema20_reclaim_fail_4h | short | 225 | +0.028 | +0.003 | +0.010 | +0.054 | +0.011 | +0.043 | -0.005 | FAIL | extra subset |
| l29_weekly_rsi_cross_4h | long | 67 | -0.069 | -0.085 | -0.027 | -0.146 | -0.122 | -0.062 | -0.045 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Notes:
- Two near misses fail only the frozen extra31 subset gate: r29 squeeze release (+0.067, extra31 -0.017) and s29 failed
  EMA20 reclaim (+0.028, extra31 -0.005). They are recorded as FAIL. The gate is not relaxed.
- Mutation check: 8 of 9 planted changes were caught. The one that was not, shifting the daily ATR by -1 inside the
  prior-day squeeze test, is not a lookahead. It only lets the prior-day squeeze use the last completed day's ATR, which
  is known when the entry is made. Shifting by -2 is a real lookahead, and the tests catch it. A broken-week stale-entry
  test was also added.
