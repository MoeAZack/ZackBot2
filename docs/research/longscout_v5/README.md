# LONGSCOUT-V5 results (research only): 0 of 6 pass

Frozen `d8822133` (rules) and `88fab578` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| s5_high_beta_btc_down_4h | short | 57 | +0.026 | +0.004 | -0.044 | +0.066 | -0.051 | -0.082 | +0.130 | FAIL | early, ex-best, n<100, fresh subset |
| r5_ichimoku_cloud_4h | both | 608.5 | +0.014 | -0.011 | +0.037 | -0.034 | -0.008 | -0.011 | +0.080 | FAIL | R23, late, ex-best, fresh subset |
| r5_kama_flip_4h | both | 1264.5 | -0.044 | -0.067 | -0.040 | -0.039 | -0.054 | -0.054 | -0.018 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |
| l5_anchored_vwap_reclaim_4h | long | 381 | -0.089 | -0.116 | -0.032 | -0.167 | -0.116 | -0.089 | -0.112 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |
| s5_btc_down_bounce_fail_4h | short | 251.5 | -0.090 | -0.148 | -0.196 | -0.022 | -0.120 | -0.111 | -0.048 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |
| l5_volume_dry_pullback_4h | long | 204.5 | -0.171 | -0.196 | -0.135 | -0.243 | -0.191 | -0.140 | -0.239 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |

Running total LONGSCOUT V1-V5: 32 new frozen candidates, 0 pass. Trend-in-regime families stay weakly positive but fail a gate;
pullback / reversal / reclaim long rules lose after costs. No post-hoc filter or parameter change is proposed from these results.
