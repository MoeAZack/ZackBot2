# LONGSCOUT-V73 results (research only): 0 of 2 pass

Frozen `5e1495db` (rules) and `1d7a618c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l73_obv_bull_div_4h | long | 188 | -0.117 | -0.139 | -0.149 | -0.085 | -0.144 | -0.098 | -0.201 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s73_obv_bear_div_4h | short | 185 | -0.161 | -0.185 | -0.212 | -0.096 | -0.190 | -0.148 | -0.212 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: both OBV-divergence rules lose clearly after costs. Tests: prefix, independent reference loop (with a removed-day gap), synthetic
divergence edges (equal OBV, equal close, tied extremes use the latest day, bearish mirror), regime gate and gap tests; 11 planted
mutants all caught (the short equal-close mutant after a synthetic test was added).
