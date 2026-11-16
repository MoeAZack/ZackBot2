# LONGSCOUT-V36 results (research only): 0 of 3 pass

Frozen `5da1ef80` (rules) and `d639c982` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s36_max_effect_4h | short | 64 | +0.147 | +0.137 | +0.110 | +0.181 | +0.098 | +0.127 | +0.190 | FAIL | n<100, order 0 30-per-half |
| r36_trend_tstat_4h | both | 221 | -0.058 | -0.081 | +0.044 | -0.174 | -0.086 | +0.012 | -0.237 | FAIL | mean R15, late, ex-best, R23, extra subset |
| l36_vol_collapse_4h | long | 133 | -0.212 | -0.239 | -0.192 | -0.257 | -0.283 | -0.205 | -0.304 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

## s36_max_effect_4h: a small-sample lead, still FAIL

Every return gate is positive, but there are only 64 closes against the 100 minimum, and order 0 has 29 early and 35 late
closes against 30 per half. It stays FAIL, and the count gate is not relaxed.
- All 40 orders are positive (worst +0.109).
- Order 0: 64 closes on 45 coins, +7.12 R15. Exits: 45 time, 17 stop, 2 target.
- Concentration: ex-top-3 coins +0.025, ex-top-5 -0.038.

The same shape as the retired `lc_volume_accum_1d` (strong but thin, and it later failed on fresh coins). Any follow-up
needs new data or forward evidence, never a looser gate or re-tuned parameters on this calendar.
