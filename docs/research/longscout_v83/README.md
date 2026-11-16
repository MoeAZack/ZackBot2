# LONGSCOUT-V83 results (research only): 0 of 2 pass

Frozen `8e312d4d` (rules) and `a44a25f6` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s83_concave_turn_down_4h | short | 183.5 | -0.044 | -0.068 | -0.023 | -0.072 | -0.066 | +0.014 | -0.184 | FAIL | mean R15, early, late, ex-best, R23, extra subset |
| l83_convex_turn_up_4h | long | 204 | -0.067 | -0.091 | -0.054 | -0.092 | -0.096 | -0.130 | +0.143 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

Notes: both rules lose after costs. Ledger: 267 trials after V83, 11 passes. Tests: prefix, independent np.polyfit reference loop over
several seeds (with a removed-day gap), synthetic parabola (exact first upturn day), V-shape, concave curve never long, missing day j-30
and inside-window gaps, zero slope / zero curvature boundaries (monkeypatched fit), regime gate with forced regimes, hour-zero.
10 planted mutants, all caught (three after boundary and day j-30 tests were added).
