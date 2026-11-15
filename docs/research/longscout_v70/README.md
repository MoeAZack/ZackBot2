# LONGSCOUT-V70 results (research only): 0 of 2 pass

Frozen `c2388e7c` (rules) and `e78f43a3` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s70_volume_dryup_near_low_4h | short | 86 | -0.056 | -0.086 | +0.198 | -0.296 | -0.096 | -0.033 | -0.288 | FAIL | n<100, mean R15, late, ex-best, R23, fresh subset, extra subset |
| r70_month_open_cross_4h | both | 298.5 | -0.097 | -0.121 | -0.098 | -0.112 | -0.122 | -0.109 | -0.104 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: both rules lose after costs. Tests: prefix, independent reference loops (including a removed-day gap), synthetic month-boundary and
dry-up edges, regime gate and gap tests; 13 planted mutants all caught (three dry-up edge mutants after a synthetic test was added).
