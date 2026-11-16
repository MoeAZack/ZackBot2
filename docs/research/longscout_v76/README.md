# LONGSCOUT-V76 results (research only): 1 of 2 pass

Frozen `b528105d` (rules) and `8ab4ab22` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s76_uptrend_line_break_4h | short | 223 | +0.035 | +0.010 | +0.018 | +0.063 | +0.009 | +0.031 | +0.056 | WIDE165_PASS | - |
| l76_downtrend_line_break_4h | long | 267 | -0.018 | -0.042 | -0.009 | -0.039 | -0.041 | -0.031 | +0.024 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

## s76_uptrend_line_break_4h: screen pass (lead only, needs Codex reproduction)

**Screen:** median R15 +0.035, n 223, R23 +0.010, ex-best +0.009, fresh134 +0.031, extra31 +0.056.

**Diagnostics** (cloud, non-official; path scenario, R = pnl15 / $8):
- 37 of 40 orders positive (median +0.035, min -0.009).
- Order 0: 228 trades, mean +0.061, max drawdown 9.64R (Standard).
- Order 0 without its best trades: ex-top3 +0.035, ex-top5 +0.018.
- Order 0 by year:

  | year | trades | mean R15 |
  |---|---|---|
  | 2022 | 9 | +0.176 |
  | 2023 | 61 | +0.013 |
  | 2024 | 57 | +0.007 |
  | 2025 | 58 | +0.108 |
  | 2026 | 43 | +0.112 |

- Largest coin contribution: APT +7.0R.

This is more even than r74 or l75, but the edge per trade is small. With 255 trials a lucky pass is expected.

**Other rule:** l76_downtrend_line_break fails.

**Tests:** prefix checks; independent reference loop (including a removed-day gap); synthetic trendline edges (line equality, previous-day position, window ends, tied anchors, mirror); regime-gate and gap tests.

**Mutants:** 14 planted, 13 caught. Two of the 13 were caught only after synthetic tests were added. The one survivor is behaviour-equivalent: extending the p1 window to j-20 cannot create a line, because day j-20 also sits in the p2 window.
