# LONGSCOUT-V8 results (research only): 0 of 5 pass

Frozen `4a2814b4` (rules) and `6376a736` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s8_dual_falling_hold_4h | short | 35 | +0.283 | +0.264 | +0.018 | +0.503 | +0.172 | +0.307 | +0.130 | FAIL | n<100, order 0 30-per-half |
| l8_near_high_momentum_4h | long | 169 | +0.005 | -0.014 | -0.119 | +0.165 | -0.104 | -0.104 | +0.239 | FAIL | early, ex-best, R23, fresh subset |
| r8_ratio_regime_flip_4h | both | 167 | -0.024 | -0.042 | +0.037 | -0.122 | -0.098 | -0.042 | -0.098 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| l8_calm_uptrend_hold_4h | long | 384.5 | -0.044 | -0.061 | +0.042 | -0.169 | -0.077 | -0.022 | -0.091 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| l8_up_streak_4h | long | 426 | -0.077 | -0.108 | -0.113 | -0.015 | -0.096 | -0.160 | +0.151 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

s8_dual_falling_hold_4h is positive on every economic gate but has only ~35 closes (fails the count gates). It is an
insufficient-sample note, not a pass and not a lead to loosen any gate. The coin-own-regime family (r6_regime_flip_4h,
l6_dual_regime_hold_4h, s8_dual_falling_hold_4h) is positive each time; the ratio-regime and calm-filter variants are not.
Running total LONGSCOUT V1-V8: 49 candidates, 1 screen pass (r6_regime_flip_4h, awaiting Codex reproduction).
Causality: every-bar prefix cuts over the last 300 bars; 2 of 3 planted one-bar lookaheads were caught (the third,
near-high, changed no signal on the synthetic path).
