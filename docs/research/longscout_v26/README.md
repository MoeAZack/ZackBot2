# LONGSCOUT-V26 results (research only): 1 of 3 pass

Frozen `6ae6612e` (rules) and `17bfc22c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s26_weekly_close_breakdown_4h | short | 101 | +0.081 | +0.057 | +0.079 | +0.094 | +0.019 | +0.099 | +0.061 | WIDE165_PASS | - |
| r26_daily_heikin_flip_4h | both | 314 | -0.006 | -0.030 | -0.018 | +0.008 | -0.026 | -0.069 | +0.193 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| l26_weekly_close_breakout_4h | long | 114 | -0.020 | -0.036 | -0.045 | +0.016 | -0.084 | -0.042 | +0.020 | FAIL | mean R15, early, ex-best, R23, fresh subset |

These are the three V25 rules (unchanged hypotheses, parameters and management) re-frozen after the timestamp-unit defect
that gave them zero trades in V25. They are not new hypotheses: 118 unique hypotheses across LONGSCOUT so far.

## s26_weekly_close_breakdown_4h: thin pass, a lead only

- 33 of 40 orders positive (worst -0.105). Order 0: 102 closes (50 early / 52 late), +17.16 R15, max drawdown 6.7 R15
  (Standard), 40 coins; exits 48 stop / 40 time / 14 target.
- Median closes 101 sits right at the 100 gate.
- Concentration: ex-top-3 coins -0.068, ex-top-5 -0.148 (median over orders). The result rests on a few coins.
- By entry year (median over orders): 2022 +1.03 (3 trades in order 0), 2023 +0.09, 2024 +0.03, 2025 +0.17, 2026 -0.01.
- With 5 passes in 118 hypotheses, luck is the likely explanation. Research only: no PAPER, no activation.
