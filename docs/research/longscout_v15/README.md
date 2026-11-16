# LONGSCOUT-V15 results (research only): 0 of 4 pass

Frozen `fa54a2ff` (rules) and `70826710` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s15_joint_btc_breakdown_4h | short | 90 | +0.096 | +0.072 | +0.065 | +0.123 | +0.002 | +0.101 | +0.055 | FAIL | n<100 |
| l15_joint_btc_breakout_4h | long | 105 | -0.007 | -0.032 | +0.107 | -0.178 | -0.134 | +0.003 | +0.021 | FAIL | mean R15, late, ex-best, R23 |
| s15_pivot_s1_breakdown_4h | short | 293 | -0.013 | -0.038 | -0.065 | +0.033 | -0.038 | +0.008 | -0.092 | FAIL | mean R15, early, ex-best, R23, extra subset |
| l15_pivot_r1_breakout_4h | long | 358 | -0.083 | -0.107 | -0.088 | -0.082 | -0.130 | -0.156 | +0.121 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

s15_joint_btc_breakdown_4h fails only the count gate (median 90 closes < 100); ex-best is barely positive (+0.002). It is an
insufficient-sample note, not a pass; no gate is loosened. Running total LONGSCOUT V1-V15: 78 candidates, 1 screen pass
(r6_regime_flip_4h, independently reproduced by Codex).
