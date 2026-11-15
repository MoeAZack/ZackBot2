# LONGSCOUT-V74 results (research only): 1 of 2 pass

Frozen `8d675af2` (rules) and `3fbb578e` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r74_prior_month_break_4h | both | 215 | +0.054 | +0.033 | +0.054 | +0.063 | +0.027 | +0.073 | +0.035 | WIDE165_PASS | - |
| l74_fractal_break_4h | long | 253.5 | -0.095 | -0.119 | -0.117 | -0.072 | -0.139 | -0.127 | -0.022 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

## r74_prior_month_break_4h: screen pass (a lead only, needs Codex reproduction)

It passes rule v1 and the frozen subset gate: median +0.054 R15, 215 closes, fresh134 +0.073, extra31 +0.035. With 251 trials,
a lucky pass is expected, so this is a lead and nothing more. The diagnostics are cloud runs and non-official. They were computed from
`r74_prior_month_break_4h_all_orders_trades.csv.gz`, path scenario, R = pnl15 / $8.

**It is fragile:**

| check | result |
|---|---|
| orders positive | 37 of 40 (median +0.054, min -0.020) |
| order 0 | 218 trades, mean +0.022 R15, max drawdown 12.47R (Standard) |
| order 0 without its best 3 / 5 trades | -0.005 / -0.024 |
| order 0 by year | 2022 +0.441 (4), 2023 -0.046 (59), 2024 +0.129 (66), 2025 -0.040 (48), 2026 -0.020 (41) |

**The short side carries it.** Short legs have a median of +0.182 across orders and are positive in 40 of 40 orders. Long legs have a median of -0.036 and are positive in only 8 of 40.

A short-only version would be a new hypothesis chosen after seeing this data. It must not be presented as an untouched pass, and it could only be judged on forward data.

The largest order-0 coin contributions are 1000LUNC +8.3R, 1000BONK +5.5R, 1000SHIB +4.1R, FET +4.1R and DASH +4.0R.
l74_fractal_break fails.

**Tests:** prefix, independent reference loops (with a removed-day gap), synthetic fractal and month edges, the fractal window,
regime-gate and gap tests. All 17 planted mutants are caught. The consec mutant was caught only after the window test was added.
