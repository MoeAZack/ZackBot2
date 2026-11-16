# LONGSCOUT-V82 results (research only): 0 of 2 pass

Frozen `05379876` (rules) and `a55e5862` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s82_liquidity_dries_4h | short | 228.5 | +0.002 | -0.024 | -0.029 | +0.033 | -0.023 | +0.031 | -0.089 | FAIL | early, ex-best, R23, extra subset |
| l82_liquidity_improves_4h | long | 207 | -0.165 | -0.181 | -0.068 | -0.317 | -0.206 | -0.227 | -0.030 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: both rules fail after costs; s82 is flat (+0.002) and fails early half, ex-best, R23 and the extra31 subset. Ledger: 265 trials
after V82, 11 passes. Tests: prefix, independent Amihud reference loop over several seeds (with a removed-day gap), synthetic new-low /
new-high edges, flat-series tie check, first-day-only check, regime gate with forced regimes, hour-zero. 10 planted mutants, all caught
(the `<=` mutant only after the flat-series test was added).
