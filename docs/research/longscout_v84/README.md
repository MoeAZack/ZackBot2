# LONGSCOUT-V84 results (research only): 0 of 2 pass

Frozen `6a351b28` (rules) and `1dd5d455` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l84_downside_variance_flush_4h | long | 122 | -0.077 | -0.101 | -0.025 | -0.137 | -0.112 | -0.056 | -0.144 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| s84_upside_variance_spike_4h | short | 135 | -0.085 | -0.106 | -0.148 | +0.000 | -0.116 | -0.009 | -0.247 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |

Notes: both rules lose after costs. Ledger: 269 trials after V84, 11 passes. Tests: prefix, independent pure-Python semivariance
reference loop over several seeds (with a removed-day gap), synthetic +8% / -8% jump after a balanced stretch (exact first cross day),
zero variance and undefined previous SJ never fire, missing day j-31 / j-30, level boundaries at exactly +-0.5 (monkeypatched SJ),
regime gate with forced regimes, hour-zero. 12 planted mutants, all caught (the `+ 1e-9` denominator mutant after the undefined
previous-SJ test was added).
