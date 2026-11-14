# LONGSCOUT-V9 results (research only): 0 of 5 pass

Frozen `e8ebd1e5` (rules) and `f7688b22` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l9_decoupled_strength_4h | long | 55 | +0.273 | +0.251 | +0.149 | +0.345 | -0.179 | -0.018 | +1.025 | FAIL | n<100, ex-best, order 0 30-per-half, fresh subset |
| s9_alt_weakness_hold_4h | short | 77 | +0.038 | +0.019 | -0.020 | +0.153 | -0.047 | +0.031 | +0.012 | FAIL | n<100, early, ex-best, order 0 30-per-half |
| r9_keltner_regime_4h | both | 419.5 | -0.023 | -0.046 | -0.009 | -0.046 | -0.045 | -0.035 | -0.012 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l9_weekly_range_breakout_4h | long | 251.5 | -0.030 | -0.053 | +0.048 | -0.139 | -0.068 | -0.038 | -0.010 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| r9_obv_trend_regime_4h | both | 757.5 | -0.056 | -0.080 | -0.085 | -0.021 | -0.071 | -0.066 | -0.031 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

l9_decoupled_strength_4h is positive on average but rests on one coin (ex-best -0.179) and on extra31 only (fresh -0.018),
with 55 closes. Nothing here is a lead. Running total LONGSCOUT V1-V9: 54 candidates, 1 screen pass (r6_regime_flip_4h,
independently reproduced by Codex, #347 comment 6874885569).
