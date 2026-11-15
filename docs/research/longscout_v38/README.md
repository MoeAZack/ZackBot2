# LONGSCOUT-V38 results (research only): 0 of 3 pass

Frozen `24979c67` (rules) and `76e598be` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l38_daily_cmf_4h | long | 143 | +0.085 | +0.062 | -0.023 | +0.309 | +0.031 | +0.020 | +0.309 | FAIL | early |
| r38_daily_aroon_4h | both | 203 | +0.045 | +0.022 | +0.034 | +0.065 | +0.021 | +0.123 | -0.124 | FAIL | extra subset |
| r38_daily_psar_flip_4h | both | 294 | -0.006 | -0.029 | -0.003 | -0.023 | -0.030 | -0.029 | +0.027 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

Two near misses, each failing exactly one gate, are recorded as FAIL with no gate relaxed:
- l38 Chaikin money flow (+0.085) fails the early half (-0.023); its gain is concentrated in the late half (+0.309).
- r38 Aroon (+0.045, fresh +0.123) fails the extra31 subset (-0.124).

Near misses like these are what chance alone produces in a search this wide (154 trials).
