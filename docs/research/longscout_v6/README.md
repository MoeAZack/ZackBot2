# LONGSCOUT-V6 results (research only): 1 of 6 passes the frozen screen

Frozen `acc8d0ee` (rules) and `eb5f615c` (cases) before any run. 165 coins (fresh134 + extra31; shared consumed calendar, not untouched).
Rule v1 + frozen fresh/extra subset gate. Cloud, non-official.

| case | side | median closes | mean R15 | R23 | early | late | ex-best | fresh134 | extra31 | status | failed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| r6_regime_flip_4h | both | 155 | +0.332 | +0.312 | +0.299 | +0.274 | +0.177 | +0.226 | +0.516 | WIDE165_PASS | - |
| l6_dual_regime_hold_4h | long | 101.5 | +0.111 | +0.094 | +0.249 | -0.147 | -0.046 | +0.104 | +0.021 | FAIL | late, ex-best |
| r6_tsmom_4h | both | 538.5 | -0.024 | -0.048 | -0.025 | -0.019 | -0.051 | -0.034 | -0.032 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |
| r6_heikin_ashi_trend_4h | both | 1670.5 | -0.035 | -0.059 | -0.051 | -0.015 | -0.042 | -0.044 | -0.004 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |
| s6_lower_high_breakdown_4h | short | 118 | -0.201 | -0.227 | -0.299 | -0.131 | -0.241 | -0.146 | -0.355 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |
| l6_rsi_bull_range_4h | long | 352 | -0.217 | -0.246 | -0.210 | -0.239 | -0.242 | -0.227 | -0.185 | FAIL | mean R15, R23, early, late, ex-best, fresh subset, extra subset |

## r6_regime_flip_4h passed every frozen gate (a screen pass, not an edge claim)
Rule: enter on the first 4h bar after the coin's own daily R1 regime turns rising (long) or falling (short); exit when
it leaves that state; 3 ATR stop, 120-bar cap. All 40 orders positive (min +0.020, p10 +0.076); worst-path scenario
identical (+0.332). Order 0: 149 closes (86 early / 63 late), +38.32 R15 at fixed $8 risk (= longs +26.71 / shorts +11.61 R15; +40.9 in
base-cost native R), exits 60 signal /
51 stop / 38 time, max closed-trade drawdown 18.1R (label: Elevated, 15-30R).

Diagnostics, written after the result and NOT gates (no rule or gate is changed by them):
- Profile is a classic trend tail (pooled path trades, all orders): median trade -0.39 / max +32.05 in base-cost native R
  (repriced R15: median -0.420, max +31.998), 75th pct +0.18 native R; 29 of 89 traded coins positive (order 0).
- Concentration: median over orders of mean R15 without the 3 best coins of each order +0.008, without the best 5 -0.089,
  without the best 10 -0.255. The edge depends on a handful of large runs (order 0: GRT, UNI, TRB, ARB, BONK).
- By entry year (all orders, path): longs 2022 -0.53 (n 29), 2023 +0.28, 2024 +1.12, 2025 +0.12, 2026 +0.77; shorts
  2022 +0.16, 2023 +0.04, 2024 +0.03, 2025 +0.34, 2026 -0.08 (R per trade).
- Multiple testing: 38 LONGSCOUT candidates so far. One pass in 38 is within what luck can produce, so this is only a lead
  for independent Codex reproduction and a forward test. No PAPER, no account change.

Other five: l6_dual_regime_hold_4h was positive (+0.111) but lost in the late half and ex-best; the rest lost after costs.
Running total LONGSCOUT V1-V6: 38 candidates, 1 screen pass (r6_regime_flip_4h).

Erratum (label only, no rerun; Codex #347 comment 6874885569): two unit labels clarified. Order-0 +40.9 was base-cost
native R, the fixed-$8 R15 total is +38.324; the pooled median/max were native R, repriced R15 values added. Gates used R15 throughout.
