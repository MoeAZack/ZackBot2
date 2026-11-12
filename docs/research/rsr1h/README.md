# RSR-1H results: FAIL (all arms, all costs, both paths)

Frozen head a8ec8bbbbde58fbb70098184237d37e47aac2c5f (contract 65597aec, runner eb0bde8e, tests 979dd3aa); run unchanged.
`rsr1h_result.json` sha256 611c7383...; order-0 trade CSVs per arm/path/cost. consumedDEV; ledger family residual_shock_reversal seq 1.

40-order medians, mean R per close (gross = order-0 gross after slippage, R/trade):
| arm | path | closes | gross | 7bp | 15bp | 23bp | 15bp early / later | 15bp ex-best | orders >0 (7bp) |
|---|---|---|---|---|---|---|---|---|---|
| union40 (primary) | causal | 2,322 (~10.8/wk) | -0.008 | -0.037 | -0.075 | -0.114 | -0.097 / -0.047 | -0.082 | 0% |
| union40 | worst | 2,105 | -0.013 | -0.024 | -0.063 | -0.102 | -0.074 / -0.048 | -0.069 | 0% |
| original9 | causal | 1,200 | -0.050 | -0.082 | -0.132 | -0.182 | -0.140 / -0.121 | -0.136 | 0% |
| extra31 | causal | 2,089 | +0.013 | -0.015 | -0.050 | -0.086 | -0.063 / -0.035 | -0.059 | 0% |
| extra31 | worst | 1,880 | +0.014 | -0.005 | -0.041 | -0.077 | -0.048 / -0.033 | -0.048 | 12.5% |

union40 gates: closes and per-period counts pass; every economic gate fails (R15 early/later, R23, ex-best, p10, worst path).
Order 0 union40 causal 15bp: 1,786 shorts (-0.079R) / 527 longs (-0.059R); exits 1,686 time, 567 stop, 60 target.
Reading: market-adjusted 3h shocks mostly do not reverse within 12 h; there is no gross edge to protect from costs. REJECTED; no rescue.
