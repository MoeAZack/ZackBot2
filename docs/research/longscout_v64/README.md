# LONGSCOUT-V64 results (research only): 0 of 2 pass

Frozen `07a7bcf1` (rules) and `2894081e` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s64_down_day_ratio_4h | short | 141 | -0.004 | -0.028 | -0.036 | +0.015 | -0.034 | -0.062 | +0.123 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| l64_up_day_ratio_4h | long | 113 | -0.125 | -0.145 | -0.093 | -0.173 | -0.173 | -0.218 | +0.163 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

Mutation check: 4 of 4 planted mutants caught (one needed a synthetic flat-day test, added before freeze).

Ledger after this round: 226 frozen, 226 evaluated, 8 passes.
