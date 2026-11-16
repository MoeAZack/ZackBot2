# LONGSCOUT-V10 results (research only): 0 of 4 pass

Frozen `f1f9ab75` (rules) and `76acca8f` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s10_base_breakdown_4h | short | 128 | +0.047 | +0.018 | +0.110 | -0.024 | -0.007 | +0.107 | -0.078 | FAIL | late, ex-best, extra subset |
| r10_efficiency_trend_4h | both | 174 | +0.031 | +0.010 | -0.081 | +0.193 | -0.056 | -0.165 | +0.602 | FAIL | early, ex-best, fresh subset |
| l10_decorrelated_rise_4h | long | 207 | +0.008 | -0.009 | +0.075 | -0.086 | -0.051 | -0.025 | +0.163 | FAIL | late, ex-best, R23, fresh subset |
| l10_base_breakout_4h | long | 173 | -0.021 | -0.057 | +0.065 | -0.149 | -0.086 | -0.046 | +0.006 | FAIL | mean R15, late, ex-best, R23, fresh subset |

Running total LONGSCOUT V1-V10: 58 candidates, 1 screen pass (r6_regime_flip_4h, independently reproduced by Codex).
