# LONGSCOUT-V88 results (research only): 0 of 2 pass

Frozen `fe6ae818` (rules) and `d9aa70c7` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l88_low_idio_vol_uptrend_4h | long | 74 | -0.131 | -0.166 | -0.434 | +0.396 | -0.289 | -0.139 | -0.064 | FAIL | n<100, mean R15, early, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |
| s88_high_idio_vol_downtrend_4h | short | 136.5 | -0.144 | -0.164 | -0.352 | -0.014 | -0.174 | -0.188 | -0.045 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

Notes: both rules lose after costs (l88 also thin, median 74 closes). Ledger: 277 trials after V88, 11 passes. Tests: prefix, independent
pure-Python residual-volatility reference loop over several seeds (with a removed coin day), OLS-residual definition check and
zero-residual (no NaN) case, boundaries for the new 120-day low/high, R exactly 0 and its base day j-60, missing day j-181
(monkeypatched IV), a missing BTC day inside the window blocks the signal, regime gate with forced regimes, hour-zero.
13 planted mutants, all caught.
