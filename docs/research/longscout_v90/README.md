# LONGSCOUT-V90 results (research only): 0 of 2 pass

Frozen `5c30c2d1` (rules) and `4289f39e` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s90_down_shock_drift_4h | short | 54 | +0.061 | +0.048 | +0.137 | -0.016 | +0.003 | +0.070 | -0.002 | FAIL | n<100, late, order 0 30-per-half, extra subset |
| l90_up_shock_drift_4h | long | 188 | +0.032 | +0.018 | +0.011 | +0.063 | -0.007 | -0.069 | +0.331 | FAIL | ex-best, fresh subset |

Notes: both rules FAIL. l90 is marginal (+0.032) and fails ex-best and the fresh134 subset (-0.069; extra31 +0.331), the same
extra31-only pattern as s85 and s87. s90 is thin (median 54 closes) and fails the late half and extra31. Ledger: 281 trials after V90,
11 passes. Tests: prefix on coins with planted +-25% / 30x-volume shock days, independent pure-Python z-score and median reference loop
over several seeds (with a removed-day gap), z and volume boundaries, median (not mean) volume base, missing day j-61, regime gate with
forced regimes, hour-zero. 12 planted mutants: 10 caught; `z >= 3` -> `z > 3` (and the short mirror) survive because an exact float
tie of the z ratio cannot be built from log-price returns; the volume tie at exactly 3x is tested.
