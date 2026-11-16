# LONGSCOUT-V87 results (research only): 0 of 2 pass

Frozen `86c448fb` (rules) and `f6b396d9` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s87_thin_tail_downtrend_4h | short | 128 | +0.024 | +0.000 | +0.050 | +0.003 | -0.013 | -0.042 | +0.217 | FAIL | ex-best, fresh subset |
| l87_thin_tail_uptrend_4h | long | 149 | -0.071 | -0.091 | -0.077 | -0.057 | -0.114 | -0.113 | +0.036 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

Notes: both rules FAIL. s87 is marginal (+0.024) and fails ex-best and the fresh134 subset (-0.042); its edge again sits in extra31.
l87 loses after costs. Ledger: 275 trials after V87, 11 passes. Tests: prefix, independent pure-Python bias-corrected kurtosis reference
loop over several seeds (with a removed-day gap), boundaries for the new 120-day low, R exactly 0 and its base day j-60, missing day
j-181 (monkeypatched kurtosis), regime gate with forced regimes, hour-zero. 12 planted mutants, all caught.
