# LONGSCOUT-V53 results (research only): 0 of 3 pass

Frozen `ea798c3c` (rules) and `fff6403a` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r53_daily_tk_cross_4h | both | 266 | -0.014 | -0.036 | +0.021 | -0.043 | -0.036 | +0.003 | -0.064 | FAIL | mean R15, late, ex-best, R23, extra subset |
| r53_daily_kst_4h | both | 261 | -0.037 | -0.060 | -0.030 | -0.028 | -0.058 | -0.042 | -0.035 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l53_daily_coppock_4h | long | 192 | -0.104 | -0.127 | -0.098 | -0.124 | -0.125 | -0.104 | -0.101 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Mutation check: 7 of 8 planted mutants caught. Changing the Coppock guard from `_consec(D, 3)` to `_consec(D, 2)` is behaviour-equivalent here: `_consec(D, 2)` already requires three consecutive days (two one-day steps), which is all the three-day turn needs.

Ledger after this round: 199 frozen, 199 evaluated, 7 passes.
