# LONGSCOUT-V4 results (research only): 0 of 6 pass

Frozen `5eb5d0bf` (rules + cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official.

| case | side | median closes | mean R15 | early | late | ex-best | fresh134 | extra31 | failed |
|---|---|---|---|---|---|---|---|---|---|
| r4_donchian55_trail_4h | both | 428.5 | +0.056 | -0.010 | +0.167 | +0.027 | -0.005 | +0.223 | early half, fresh subset |
| r4_supertrend_regime_4h | both | 199.5 | +0.030 | +0.034 | +0.031 | -0.037 | +0.011 | +0.105 | ex-best coin |
| l4_rs_leader_ema20_reclaim_4h | long | 205.5 | -0.045 | -0.095 | +0.054 | -0.078 | -0.123 | +0.188 | most |
| s4_rs_laggard_short_4h | short | 224.5 | -0.043 | -0.051 | -0.027 | -0.070 | +0.036 | -0.250 | all value gates |
| s4_failed_breakout_short_4h | short | 516 | -0.118 | -0.141 | -0.086 | -0.136 | -0.135 | -0.071 | all |
| l4_failed_breakdown_long_4h | long | 527 | -0.140 | -0.142 | -0.134 | -0.157 | -0.156 | -0.104 | all |

Observation across V1-V4 (not a pass, not a filter): trend-following in the coin's own R1 direction on both sides
(rs_regime_gated_trend, r3_regime_ema_cross, r4_supertrend_regime, r4_donchian55_trail) is repeatedly slightly positive
(+0.03 to +0.06 R) but each version fails one gate (a half, ex-best or the fresh subset). Reversal / failed-break rules
lose clearly after costs.
