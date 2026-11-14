# LONGSCOUT-V7 results (research only): 0 of 6 pass

Frozen `a25f894b` (rules) and `e6ca09cc` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r7_regression_tstat_4h | both | 201.5 | -0.013 | -0.038 | +0.057 | -0.125 | -0.137 | -0.046 | -0.018 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| r7_ema_ribbon_alignment_4h | both | 391 | -0.030 | -0.054 | -0.048 | +0.021 | -0.064 | -0.030 | -0.028 | FAIL | mean R15, early, ex-best, R23, fresh subset, extra subset |
| s7_blowoff_fade_short_4h | short | 139 | -0.067 | -0.079 | -0.061 | -0.078 | -0.088 | +0.027 | -0.346 | FAIL | mean R15, early, late, ex-best, R23, extra subset |
| r7_aroon_regime_4h | both | 333 | -0.087 | -0.111 | -0.023 | -0.159 | -0.121 | -0.095 | -0.059 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l7_nr7_breakout_4h | long | 448 | -0.134 | -0.161 | -0.119 | -0.162 | -0.153 | -0.093 | -0.247 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l7_btc_regime_turn_long_4h | long | 22 | -0.307 | -0.326 | -0.043 | -0.631 | -0.455 | -0.262 | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

extra31 n/a = some orders had no extra31 trades (median undefined), counted as a fail.
Running total LONGSCOUT V1-V7: 44 candidates, 1 screen pass (r6_regime_flip_4h, awaiting Codex reproduction).
