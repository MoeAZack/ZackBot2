# LONGSCOUT-V2 results (research only): 0 of 6 pass

Frozen at `ebc5ec33` (rules) / `55eba1c1` (cases, 165-coin universe, rule v1 + subset gate); runner argparse fix `0a8c554f`
before any run. 165 coins = 134 fresh (data lock 28d7b7c8) + extra31. Cloud, non-official. No account/key/runtime action.

| case | side | median closes | mean R15 | early | late | ex-best | fresh134 | extra31 | verdict |
|---|---|---|---|---|---|---|---|---|---|
| s2_high_rollover_short_4h | short | 194 | +0.081 | -0.009 | +0.231 | +0.055 | +0.124 | -0.065 | FAIL (early half, extra31 subset) |
| l2_crash_base_reclaim_1d | long | 18 | +0.193 | +0.217 | +0.148 | -0.045 | -0.192 | +2.20 (n 3) | FAIL (count, ex-best, fresh) |
| r2_regime_donchian55_1d | both | 37 | +0.022 | -0.002 | +0.071 | -0.218 | -0.102 | +0.521 | FAIL |
| r2_alt_btc_ratio_trend_1d | both | 69 | -0.154 | -0.205 | -0.087 | -0.230 | -0.192 | -0.074 | FAIL |
| l2_weekly_rs_breakout_1d | long | 37 | -0.392 | -0.255 | -0.561 | -0.482 | -0.480 | -0.152 | FAIL |
| l2_btc_flush_leader_1d | long | 0 | n/a | | | | | | FAIL (no signal: every 2-ATR BTC down day already left the rising regime) |

Daily ideas again produce too few closes under max-one. The 4h rollover short is the closest (positive overall and on the
134 fresh coins, both later half and ex-best positive) but loses in the early half and on the 31 coins; it stays a FAIL.

Disclosed: the subset-gate code crashed on a case with zero trades (empty trade file); fixed to report "no trades".
