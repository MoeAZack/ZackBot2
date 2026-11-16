# LONGSCOUT-V11 results (research only): 0 of 4 pass

Frozen `73010af8` (rules) and `7c5b188f` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s11_mature_downtrend_breakdown_4h | short | 91 | +0.081 | +0.054 | -0.057 | +0.181 | +0.020 | +0.013 | +0.267 | FAIL | n<100, early |
| l11_btc_capitulation_buy_4h | long | 21 | +0.028 | +0.011 | -0.044 | +0.099 | -0.124 | +0.034 | -0.023 | FAIL | n<100, early, ex-best, order 0 30-per-half, extra subset |
| r11_vwap30_regime_cross_4h | both | 410 | +0.017 | -0.007 | +0.005 | +0.047 | -0.026 | -0.064 | +0.239 | FAIL | ex-best, R23, fresh subset |
| l11_mature_trend_breakout_4h | long | 145 | +0.006 | -0.023 | +0.075 | -0.199 | -0.137 | -0.102 | +0.330 | FAIL | late, ex-best, R23, fresh subset |

Running total LONGSCOUT V1-V11: 62 candidates, 1 screen pass (r6_regime_flip_4h, independently reproduced by Codex).
