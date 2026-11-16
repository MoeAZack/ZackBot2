# LONGSCOUT-V81 results (research only): 0 of 2 pass

Frozen `a2d29c84` (rules) and `6575a738` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r81_variance_ratio_4h | both | 175 | -0.060 | -0.082 | -0.110 | +0.007 | -0.092 | -0.163 | +0.205 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| s81_unconfirmed_rally_4h | short | 142 | -0.088 | -0.110 | -0.203 | +0.056 | -0.122 | -0.092 | -0.083 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |

Notes: both rules lose after costs. Ledger: 263 trials after V81 (corrected count), 11 passes. Tests: prefix, independent reference
loops (with a removed-day gap), synthetic rally and variance-ratio edges (runs vs alternation, zero variance, 40-day-back gap, exact
+15%), an inclusive first-cross check, regime gate with forced regimes. 15 planted mutants: 14 caught (four after tests were added);
`v1 <= 0` -> `v1 < 0` is behaviour-equivalent (0/0 is NaN either way, only a warning differs).
