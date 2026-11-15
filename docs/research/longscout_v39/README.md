# LONGSCOUT-V39 results (research only): 0 of 3 pass

Frozen `882c6a8d` (rules) and `b3694075` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r39_daily_vortex_4h | both | 276.5 | -0.026 | -0.050 | +0.029 | -0.093 | -0.049 | -0.037 | -0.018 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| r39_daily_force_index_4h | both | 324 | -0.036 | -0.061 | -0.023 | -0.065 | -0.057 | -0.032 | -0.078 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l39_mass_index_bulge_4h | long | 77 | -0.370 | -0.390 | -0.317 | -0.521 | -0.443 | -0.448 | -0.196 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Mutation check: 5 of 6 planted changes were caught. The one not caught, using the next day's close in the Vortex true
range, does not change behaviour: the true range is the shared denominator of VI+ and VI-, so it cannot change which one
is larger. It is not an exploitable lookahead.
