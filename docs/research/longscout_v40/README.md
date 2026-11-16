# LONGSCOUT-V40 results (research only): 1 of 3 pass

Frozen `87b131b6` (rules) and `28c0945a` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l40_dual_momentum_4h | long | 111 | +0.091 | +0.075 | +0.148 | +0.011 | +0.048 | +0.003 | +0.318 | WIDE165_PASS | - |
| s40_relative_weakness_4h | short | 92 | +0.060 | +0.040 | +0.032 | +0.097 | +0.008 | +0.079 | +0.039 | FAIL | n<100 |
| r40_trend_consensus_4h | both | 234 | -0.120 | -0.142 | -0.122 | -0.114 | -0.143 | -0.089 | -0.242 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

## l40_dual_momentum_4h: thin pass, a lead only

- 39 of 40 orders are positive (worst -0.022). Order 0: 111 closes (66 early / 45 late) on 80 coins, +6.81 R15, max
  drawdown 12.4 R15 (Standard). Exits: 59 stop / 34 time / 18 target.
- The fresh134 median is +0.003, essentially zero. The pass comes from extra31 (+0.318).
- Concentration: ex-top-3 coins -0.013, ex-top-5 -0.070.
- By entry year (median over orders): 2022 -1.03, 2023 -0.03, 2024 +0.35, 2025 -0.30, 2026 +0.44. Three of five years
  are negative.

With 6 screen passes in 160 trials, chance is the likely explanation. Research only: no PAPER, no activation.
s40 relative weakness (+0.060, every return gate positive) fails only the trade count (92 < 100).
