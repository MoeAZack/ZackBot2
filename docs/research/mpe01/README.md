# MPE-01 results (exploratory, consumedDEV)

Frozen head c557227094696a32a11424fab8283b4dec3122a9 (contract e9ffcc4f, runner cb7ca7a0, tests 8269627d); run unchanged.
Result file `mpe01_result.json` (sha256 71b748c4...). Medians across 40 tie orders, mean R per close after costs.

| path | arm/costs | closes | meanR | p10 | early(<2025-10) | later | ex-best | closed DD R | orders >0 |
|---|---|---|---|---|---|---|---|---|---|
| m15 | maker base | 125 | +0.038 | -0.004 | -0.045 | +0.130 | -0.011 | 8.9 | 85% |
| m15 | maker +8 | 125 | +0.008 | -0.034 | -0.070 | +0.095 | -0.039 | 10.0 | 58% |
| m15 | maker +16 | 125 | -0.021 | -0.064 | -0.096 | +0.060 | -0.067 | 11.4 | 20% |
| m15 | maker alltaker | 125 | +0.033 | -0.010 | -0.050 | +0.124 | -0.016 | 9.1 | 80% |
| m15 | taker 7bp | 165 | +0.067 | +0.058 | -0.011 | +0.147 | +0.034 | 11.5 | 100% |
| m15 | taker 15bp | 165 | +0.036 | +0.027 | -0.037 | +0.112 | +0.003 | 12.5 | 100% |
| m15 | taker 23bp | 165 | +0.005 | -0.004 | -0.063 | +0.077 | -0.028 | 14.7 | 75% |
| worst | maker base | 125 | +0.047 | +0.004 | -0.037 | +0.137 | -0.006 | 8.8 | 95% |
| worst | taker 7bp | 165 | +0.069 | +0.060 | +0.004 | +0.142 | +0.035 | 11.2 | 100% |

Order-0 maker book: 314 posted, 125 filled (fill rate ~0.40), 189 unfilled (186 never reached L-2 ticks, 3 touch only, 0 volume-short), 23 post-only rejects.
Signal-level (portfolio-free, m15 base, 1,319 signals): maker-filled 490 signals earn +0.103R as maker vs +0.022R as taker;
unfilled 709 earn +0.087R as taker; post-only rejects 120 earn +0.065R as taker. The limit improves price on what fills, but fills are
adversely selected toward weaker signals, so the passive book trails the taker book. Anomaly-hour lots: 1 (maker) / 1 (taker) in order 0.

Reading: MPE-01 does NOT show that passive entry improves X2; matched taker is better on every comparable row. The taker positive here is
mostly the post-2025-10 half on already-consumed coins and a shorter 2024-10..2026-09 window; it does not rewrite X2-WIDE FAILED.
