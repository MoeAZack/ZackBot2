# LONGSCOUT-V14 results (research only): 0 of 4 pass

Frozen `89f3c376` (rules) and `30e6a3c6` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s14_ema200_loss_btc_down_4h | short | 50 | +0.275 | +0.249 | +0.439 | +0.175 | +0.197 | -0.033 | +0.779 | FAIL | n<100, order 0 30-per-half, fresh subset |
| r14_dmi_cross_regime_4h | both | 574.5 | -0.083 | -0.107 | -0.052 | -0.134 | -0.100 | -0.083 | -0.100 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |
| l14_volume_breakout_55_4h | long | 193 | -0.085 | -0.105 | -0.106 | -0.036 | -0.175 | -0.237 | +0.315 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |
| l14_ema200_reclaim_btc_up_4h | long | 102 | -0.214 | -0.237 | -0.377 | +0.126 | -0.287 | -0.279 | +0.019 | FAIL | mean R15, early, ex-best, R23, fresh subset |

s14 is positive but rests on extra31 (fresh134 -0.033) with 50 closes: not a lead.
Running total LONGSCOUT V1-V14: 74 candidates, 1 screen pass (r6_regime_flip_4h, independently reproduced by Codex).
