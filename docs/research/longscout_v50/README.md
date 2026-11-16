# LONGSCOUT-V50 results (research only): 0 of 3 pass

Frozen `dcc62887` (rules) and `53307ad3` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l50_atr_pullback_4h | long | 78.5 | -0.028 | -0.047 | -0.022 | -0.030 | -0.059 | -0.040 | +0.059 | FAIL | n<100, mean R15, early, late, ex-best, R23, fresh subset |
| l50_elder_impulse_4h | long | 359 | -0.090 | -0.117 | -0.106 | -0.092 | -0.119 | -0.067 | -0.175 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s50_atr_rebound_4h | short | 113 | -0.136 | -0.158 | -0.251 | -0.008 | -0.177 | -0.139 | -0.129 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Ledger after this round: 190 frozen, 190 evaluated, 6 passes.
