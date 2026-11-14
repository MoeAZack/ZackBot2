# LONGSCOUT-V1 results (research only)

Frozen at `465808a2` (rules at `a4e3b759`) before any outcome. Rule = Codex catalog-and-sieve rule v1, unchanged
(max one position, $8 risk / $3,333.33 notional cap = $4,000-account proxy, 40 coin orders, 15 bp, path scenario,
31 untouched coins, BTC context only). Cloud run, non-official; no account, key, runtime, scheduler or installer action.

## New candidates: 0 of 8 pass

| case | tf | side | median closes | mean R @15bp | early | late | without best coin | failed gates |
|---|---|---|---|---|---|---|---|---|
| lc_volume_accum_1d | 1d | long | 65 | **+0.311** | +0.280 | +0.356 | +0.227 | trades (<100), order-0 30/half |
| lc_trend_pullback_1d | 1d | long | 64 | +0.057 | -0.089 | +0.299 | -0.029 | trades, early, ex-best |
| rs_regime_gated_trend_4h | 4h | both | 224.5 | +0.041 | +0.080 | -0.008 | +0.006 | late |
| lc_washout_reclaim_1d | 1d | long | 38 | -0.015 | +0.318 | -0.389 | -0.107 | most |
| lc_mss_higher_low_4h | 4h | long | 143 | -0.025 | +0.007 | -0.069 | -0.087 | mean, late, ex-best |
| lc_rs_rsi2_dip_4h | 4h | long | 561 | -0.054 | -0.034 | -0.082 | -0.064 | all value gates |
| ss_ma_rejection_short_1d | 1d | short | 80 | -0.058 | -0.144 | -0.003 | -0.126 | all |
| lc_dual_regime_breakout_1d | 1d | long | 30 | -0.320 | +0.140 | -0.760 | -0.582 | all |

`lc_volume_accum_1d` fails only on sample size: every one of the 40 orders is positive (min +0.134 R, p10 +0.187),
the worst-path diagnostic keeps every value gate, 24 coins traded / 15 positive, order-0 closed max DD 5.4 R.
It is a lead, not a pass: about 0.3 closes per week under max-one. Nothing is loosened to rescue it.
Eight candidates were run together, so one good-looking result is not unusual by chance.

## Regime check: exp_trend (Trend L/S 4h)

Rule R1 (frozen): 100-day SMA of completed daily closes and its 20-day slope; read at each trade's decision time.
Runner reproduces #340 order 0 exactly (323 closes, net15 +$304.455825).

Extra31 (rule v1 PASS, 40 orders, medians): longs +3.5 R total (late half -3.5 R); shorts +35.8 R (early +21.7, late +14.6).
Short profit by the coin's own regime (median over orders):

| coin regime at entry | short closes | mean R | sum R |
|---|---|---|---|
| rising | 27 | +0.70 | +18.7 |
| sideways | 24 | +0.43 | +10.1 |
| falling | 100 | +0.02 | +1.8 |

By BTC regime the shorts make +25.2 R while BTC was rising and -1.9 R while BTC was falling.
Time share on extra31: coins falling 47%, rising 23%; BTC rising 46%, falling 30%.

Original nine (same rule): FAIL overall; longs +12.7 R, shorts -13.3 R (shorts in falling coins -16.4 R).

Reading against the frozen criterion: **not** "structure reading" in the textbook sense (shorts do not earn in falling
regimes, and the short edge disappears on the big coins). Not one-coin luck either (both halves positive, ex-best positive).
It is a period/universe effect: alts in 2022-2026 often rolled over while BTC rose, and the strategy shorts those
breakdowns. It holds only while that alt-weakness persists. The gated version that only trades with the regime
(rs_regime_gated_trend_4h) fails its late half, which agrees.

## Amendment (disclosed)
`longscout_regime.py` failed to import `strategies` (path bug) on its first call; the fix only adds the repo root to
`sys.path`. No rule, regime definition or report field changed.

## Not done
Codex independent reproduction; Windows run; adversarial review agent. Mutation check: the lookahead mutant
(daily value usable on the same day) is caught; behaviour-only mutants (fractal offset, washout bar) survive because
the tests check causality, not golden values.
