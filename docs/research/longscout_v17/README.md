# LONGSCOUT-V17 results (research only): 1 of 4 pass

Frozen `6e3f3753` (rules) and `0f8d474c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official. Failure lists read from each result JSON's `gates` block.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r17_ema50_envelope_regime_4h | both | 280.5 | +0.117 | +0.097 | +0.058 | +0.176 | +0.063 | +0.014 | +0.314 | WIDE165_PASS | - |
| s17_outside_day_bear_4h | short | 140 | -0.000 | -0.024 | +0.033 | -0.022 | -0.034 | +0.045 | -0.095 | FAIL | mean R15, late, ex-best, R23, extra subset |
| l17_three_green_days_4h | long | 222 | -0.096 | -0.119 | -0.187 | +0.052 | -0.120 | -0.134 | +0.062 | FAIL | mean R15, early, ex-best, R23, fresh subset |
| l17_outside_day_bull_4h | long | 157 | -0.120 | -0.144 | -0.164 | -0.042 | -0.228 | -0.105 | -0.197 | FAIL | mean R15, early, late, ex-best, R23, fresh subset, extra subset |

## r17_ema50_envelope_regime_4h passed every frozen gate (screen pass, not an edge claim)
Rule: close crosses above EMA50 x 1.05 while the coin is rising (R1) -> long; below EMA50 x 0.95 while falling -> short;
exit on a close back through EMA50; stop 2.5 ATR, 120-bar cap. 39 of 40 orders positive (min -0.028, p10 +0.033);
worst-path medians identical. Order 0: 269 closes (160 / 109), +44.98 R15 at fixed $8 (longs +17.58 / shorts +27.39),
exits 220 signal / 42 stop / 7 time, max closed drawdown 13.2 R15 (Standard).

Diagnostics written after the result, NOT gates:
- Fresh-coin margin is thin: fresh134 +0.014 vs extra31 +0.314.
- Trend-tail profile: median trade -0.43 R15, max +25.5. Without each order's best 3 coins -0.007, best 5 -0.056.
- By entry year (R15 per trade, short / long): 2022 +1.34 / -0.46, 2023 +0.03 / +0.17, 2024 -0.10 / -0.01,
  2025 +0.12 / +0.03, 2026 +0.05 / +0.79.
- Same family as r6_regime_flip_4h (trend in the coin's own R1 direction), so it is NOT independent evidence and would
  overlap r6 in an account. 1 more pass in 87 candidates; a lead only for Codex reproduction.

Running total LONGSCOUT V1-V17: 87 candidates, 3 screen passes (r6 reproduced by Codex; s16 weak; r17 thin on fresh coins).

Codex review (#347 comment 6875668363, evidence 72ca9f06): r17 independently reproduced (all 40 orders). Not literally r6
(exact tuple Jaccard 1.15%) but related exposure (sides agree 70.7% when both hold). Decision: research lead kept behind r6,
not a second canary; any later proposal needs venue/cost fit and preregistered paired future evidence.
