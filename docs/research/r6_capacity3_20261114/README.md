# R6-CAPACITY3-D01 — dependent capacity diagnostic (max 3 positions)

**Label: DEPENDENT_SECONDARY_CAPACITY_VARIANT.** Not a new strategy, not a substitute for the max1 result (PR #349), not an activation. First canary stays max1. Consumed calendar.

**Result: CAPACITY3_PASS** (all rule-v1 gates and the fresh132/extra31 subset gate pass; worst-path also passes).

## Freeze
Wrapper `research/extra31_screen/r6_cap3_confirm.py` frozen at **96170f26** before any max3 outcome (sha256 fad19bd9…). Same as R6-TNET163-FIT-D01 (acc8d0ee, frozen 163 list 50f6e767…, current filters a34e7950…, D01 inputs, warmup/split, $8 risk, $3,333.33 aggregate cap, 40 orders path/worst, 7/15/23 bp, funding, exits) except max concurrent positions 1 → 3. Native admission in `backtest.run`: the cap is account-wide notional shared by open positions from the start; no max1 tapes overlaid. A max1 control through the same harness equals PR #349 per order (max abs diff 0, path and worst).

## Medians of 40 orders (path)
| | max1 control | max3 |
|---|---|---|
| closes n | 150.5 | 465 |
| closes / week | 0.70 | 2.17 |
| mean R15 / R7 / R23 | +0.329 / +0.347 / +0.310 | +0.224 / +0.244 / +0.204 |
| early / later R15 | +0.405 / +0.292 | +0.282 / +0.184 |
| ex-best-coin R15 | +0.187 | +0.160 |
| fresh132 / extra31 R15 | +0.245 / +0.617 | +0.174 / +0.336 |
| ex-top-3 coins R15 (orders >0) | −0.024 (18/40) | +0.070 (34/40) |
| ex-top-5 coins R15 | −0.111 | +0.004 |
| closed sum R15 | +48.8 | +105.5 |
| closed max DD R15 (median / worst order) | 14.0 / 24.2 | **31.1 / 54.0** |
| marked max DD R (base cost, median / worst) | 16.7 / 32.7 | **32.7 / 50.5** |
| terminal open mark R (base) | +1.3 | +3.7 |
| order spread R15 p10 / min / max | +0.129 / +0.030 / +0.793 | +0.132 / +0.083 / +0.407 |

Order 0 max3: 431 closes (247/184 halves), +122.9 R15 (long +92.1 / short +30.8), closed DD 28.4 R15, 2.01 closes/week, max 3 concurrent. Refusals: 0 exchange-filter skips, 0 risk-rule blocks, 0 entries shrunk by the $3,333.33 aggregate cap in the median order.

## Risk read (reported, no new gate)
- max3 triples frequency and roughly doubles total profit, but profit per trade drops by about a third and drawdown about doubles.
- DD label moves from Elevated to **High** (median closed DD 31 R15 ≈ $249 at $8; worst order 54 R15 ≈ $432 ≈ 10.8% of $4,000, past the 10% account DD stop).
- Concentration is better at max3 (ex-top-3 positive in 34/40 orders).

## Not checked
Live runtime path; adversarial agent / mutation / CP1252 (research only, no engine code changed). Sandbox run (pandas 3.0.5, numpy 2.5.3), non-official.

No account, key, position, runtime, scheduler or trading action. No max5/grid/tuning.
