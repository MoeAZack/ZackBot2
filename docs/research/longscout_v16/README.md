# LONGSCOUT-V16 results (research only): 1 of 5 pass

Frozen `b9ac5a40` (rules) and `fab1040e` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s16_inside_day_breakdown_4h | short | 227 | +0.048 | +0.024 | +0.073 | +0.022 | +0.017 | +0.014 | +0.133 | WIDE165_PASS | - |
| l16_btc_breakout_participation_4h | long | 75 | +0.028 | +0.007 | -0.007 | +0.098 | -0.051 | +0.062 | -0.044 | FAIL | n<100, early, ex-best, extra subset |
| r16_hull_turn_regime_4h | both | 437.5 | -0.024 | -0.048 | +0.033 | -0.088 | -0.054 | -0.010 | -0.039 | FAIL | mean R15, late, ex-best, R23, fresh subset, extra subset |
| l16_inside_day_breakout_4h | long | 249.5 | -0.030 | -0.056 | -0.076 | +0.029 | -0.061 | -0.087 | +0.154 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| s16_btc_breakdown_participation_4h | short | 53 | -0.063 | -0.080 | -0.040 | -0.088 | -0.145 | -0.103 | n/a | FAIL | n<100, mean R15, early, late, ex-best, R23, order 0 30-per-half, fresh subset, extra subset |

## s16_inside_day_breakdown_4h passed every frozen gate, but weakly (a screen pass, not an edge claim)
Rule: the last completed UTC day was an inside day; a 4h close crosses below its low while the coin is falling (R1);
stop 2.5 ATR, 2.5R target, 30 bars. All 40 orders positive (min +0.003, p10 +0.020); worst-path medians identical.
Order 0: 223 closes (113 early / 110 late), +10.71 R15 at fixed $8, exits 111 time / 87 stop / 25 target, max closed
drawdown 8.8 R15 (Standard), 47 of 95 traded coins positive.

Diagnostics written after the result, NOT gates (nothing is changed by them):
- Thin margins: mean +0.048 R15, ex-best +0.017, fresh134 +0.014, R23 +0.024.
- Concentration: median over orders without each order's best 3 coins -0.031, best 5 -0.069, best 10 -0.144.
- By entry year (all orders, R15 per trade): 2022 +0.62 (n 250), 2023 +0.08, 2024 -0.00, 2025 +0.10, 2026 -0.07.
- Its long mirror (l16_inside_day_breakout_4h) fails at -0.030.
- Multiple testing: this is 1 pass in 83 LONGSCOUT candidates. With margins this small, a lucky pass is the likeliest
  reading. It is a lead only for Codex reproduction; no PAPER, no account change proposed.

Running total LONGSCOUT V1-V16: 83 candidates, 2 screen passes (r6_regime_flip_4h reproduced by Codex; s16 new, weak).
