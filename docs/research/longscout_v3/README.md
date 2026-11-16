# LONGSCOUT-V3 results (research only): 0 of 6 pass

Frozen at `0cea0536` (rules) / `4c8800cc` (cases) before any run. 165 coins = 134 fresh-to-this-search + extra31; these share
the consumed 2022-2026 calendar, so they are NOT untouched data. Rule v1 + the frozen fresh/extra subset gate. Cloud, non-official.

| case | side | median closes | mean R15 | early | late | ex-best | fresh134 | extra31 | failed |
|---|---|---|---|---|---|---|---|---|---|
| r3_regime_ema_cross_4h | both | 228.5 | +0.034 | -0.002 | +0.096 | +0.005 | +0.003 | +0.123 | early half only |
| s3_btc_up_laggard_breakdown_4h | short | 94 | +0.080 | +0.110 | +0.055 | +0.034 | -0.003 | +0.465 | count, order-0/half, fresh subset |
| l3_rising_breakout_4h | long | 214 | -0.073 | -0.175 | +0.164 | -0.130 | -0.128 | +0.094 | most |
| l3_impulse_continuation_4h | long | 254.5 | -0.041 | -0.007 | -0.112 | -0.066 | -0.059 | -0.030 | all value gates |
| l3_dual_rising_ema50_pullback_4h | long | 225 | -0.089 | -0.074 | -0.088 | -0.113 | -0.110 | -0.062 | all value gates |
| r3_sideways_band_fade_4h | both | 614.5 | -0.091 | -0.107 | -0.066 | -0.102 | -0.043 | -0.211 | all value gates |

Closest: r3_regime_ema_cross_4h misses only the early half (-0.0018). Not rescued. Every long-only rule lost again.
