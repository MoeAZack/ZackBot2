# LONGSCOUT-V67 results (research only): 0 of 3 pass

Frozen `f8462042` (rules) and `b369b5c2` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l67_52w_high_4h | long | 49.5 | +0.114 | +0.096 | +0.104 | +0.157 | +0.060 | +0.180 | -0.063 | FAIL | n<100, order 0 30-per-half, extra subset |
| r67_regression_channel_break_4h | both | 255 | -0.057 | -0.077 | -0.072 | -0.036 | -0.081 | -0.082 | -0.035 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s67_52w_low_4h | short | 53 | -0.125 | -0.145 | -0.179 | -0.102 | -0.169 | -0.180 | -0.003 | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

Mutation check: 6 of 6 planted mutants caught.

Ledger after this round: 234 frozen, 234 evaluated, 8 passes.
