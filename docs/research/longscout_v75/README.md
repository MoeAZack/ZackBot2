# LONGSCOUT-V75 results (research only): 1 of 2 pass

Frozen `a330f2af` (rules) and `3456dc0d` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| l75_ascending_triangle_4h | long | 140 | +0.040 | +0.016 | +0.052 | +0.005 | +0.006 | +0.046 | +0.017 | WIDE165_PASS | - |
| s75_descending_triangle_4h | short | 118 | -0.115 | -0.139 | -0.083 | -0.141 | -0.148 | -0.193 | +0.080 | FAIL | mean R15, early, late, ex-best, R23, fresh subset |

## l75_ascending_triangle_4h: marginal screen pass (lead only, needs Codex reproduction)

It passes rule v1 and the subset gate, but only just:

| measure | value |
|---|---|
| median mean R15 | +0.040 |
| ex-best | +0.006 |
| late half | +0.005 |
| fresh134 | +0.046 |
| extra31 | +0.017 |

**Diagnostics** (cloud, non-official; path scenario, R = pnl15 / $8):

| check | result |
|---|---|
| orders positive | 27 of 40 (median +0.040, min -0.065) |
| order 0 trades | 137 |
| order 0 mean | +0.051 |
| order 0 max drawdown | 10.91R (Standard) |
| order 0 ex-top3 | +0.008 |
| order 0 ex-top5 | -0.022 |

Order 0 by year:

| year | trades | mean |
|---|---|---|
| 2023 | 48 | +0.063 |
| 2024 | 43 | +0.053 |
| 2025 | 21 | -0.243 |
| 2026 | 24 | +0.301 |

Weaker than r74. With 253 trials a lucky pass is expected.

s75_descending_triangle fails.

**Tests:**
- prefix checks;
- independent reference loop, including a removed-day gap;
- synthetic triangle edges (touch tolerance, touch spacing, slope, break equality, early-window boundary);
- regime gate and gap tests.

All 14 planted mutants are caught. The early-window mutant was caught only after a synthetic test was added.
