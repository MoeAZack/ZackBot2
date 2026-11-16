# LONGSCOUT-V24 results (research only): 1 of 3 pass

Frozen `5a1cb564` (rules) and `4f54168b` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r24_return_rank_4h | both | 115 | +0.554 | +0.535 | +1.520 | -0.229 | -0.137 | -0.172 | +2.392 | FAIL | late, ex-best, fresh subset |
| s24_breakdown_day_4h | short | 187 | +0.065 | +0.055 | +0.064 | +0.072 | +0.049 | +0.091 | -0.083 | FAIL | extra subset |
| l24_breakaway_day_4h | long | 318 | +0.060 | +0.047 | +0.026 | +0.111 | +0.040 | +0.063 | +0.035 | WIDE165_PASS | - |

## l24_breakaway_day_4h passed every frozen gate, thinly (screen pass, not an edge claim)
Rule: the last completed UTC day rose >= 8% on >= 3x its prior-30-day median volume; enter on the first 4h bar of the next
day (ungated); stop 2.5 ATR, 2R, 30 bars. 39 of 40 orders positive (min -0.007, p10 +0.015); worst-path medians identical.
Order 0: 320 closes (191 / 129), +11.80 R15 at fixed $8, exits 139 stop / 103 time / 78 target, max closed drawdown
19.2 R15 (Elevated), 57 of 123 traded coins positive.

Diagnostics written after the result, NOT gates:
- Thin: mean +0.060 R15, early +0.026, ex-best +0.040; without each order's best 3 coins +0.006, best 5 -0.025.
- By entry year (all orders, R15 per trade): 2022 -0.13, 2023 +0.16, 2024 -0.08, 2025 +0.22, 2026 -0.02. Three of five
  years lose; the result rests on 2023 and 2025.
- Multiple testing: 1 more pass in 114 candidates. Most likely reading: luck. Lead only for Codex reproduction.

Other cases: s24_breakdown_day_4h (+0.065) fails only the extra31 subset (-0.083); r24_return_rank_4h (+0.554) is driven by
extra31 (+2.39; fresh134 -0.17) and loses in the late half.
Running total LONGSCOUT V1-V24: 114 candidates, 4 screen passes (r6 proceeding alone; s16 record only; r17 lead behind r6;
l24 new, thin).
