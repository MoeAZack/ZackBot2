# LONGSCOUT-V71 results (research only): 0 of 2 pass

Frozen `27003b23` (rules) and `3b04c3ca` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s71_rs_leads_low_4h | short | 227 | +0.022 | -0.005 | -0.029 | +0.075 | -0.012 | +0.094 | -0.170 | FAIL | early, ex-best, R23, extra subset |
| l71_rs_leads_high_4h | long | 139 | -0.141 | -0.162 | +0.094 | -0.477 | -0.202 | -0.091 | -0.259 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |

Notes: neither rule passes. Tests: prefix, independent reference loop (with a removed BTC day), long and short synthetic strictness and
first-day tests, regime gate and coin-gap tests. 10 planted mutants; 9 caught (two short-side mutants after a synthetic test was added),
and `shift().rolling` vs `rolling().shift()` is behaviour-equivalent.
