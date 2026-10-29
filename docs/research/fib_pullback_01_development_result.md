# FIB-PULLBACK-01 development result

Decision: **REJECT CURRENT VERSION / DO NOT PROMOTE**  
Evaluated: 28 October 2026, Cairo  
Exact code: `333bfcd15b41b8b5231aa10fdc522a185bfb73a6` (clean tree)  
Window: 14 October 2024 00:00 UTC through 28 September 2026 00:00 UTC, end-exclusive  
Sealed holdout: **not read**

The bounded shared candidate runner evaluated long and short as separate cells with next-open entries, 5 bp taker fee
per fill, 2 bp slippage per fill and 0.5 bp funding per 4h bar. The requested CORE-10 tier yielded nine complete symbols;
HYPEUSDT was excluded as `INCOMPLETE` (1,590 missing bars). The runner labels both packets `non_promotable` because the
legacy exchange floor does not model current step/minimum-quantity rules.

| Side | Raw signals | Closed trades | Net P&L | Net R | Mean R | Win rate | PF | Max equity DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Long | 44 | 39 | -$29.5291 | -5.8670R | -0.1504R | 35.90% | 0.7008 | -9.85% |
| Short | 74 | 67 | -$79.6598 | -16.8543R | -0.2516R | 37.31% | 0.5405 | -18.16% |

Long was negative above and below the BTC EMA200 regime. Short was also negative in both regimes. Positive isolated
symbol rows (for example XRP long and SUI short) are small, selected subgroups from a losing aggregate and are not
promotion evidence.

Packets:

- Long: `C:\Dev\fib_pullback_long_dev.json`, SHA-256
  `e0ea2c858891c1b82163352b0b266cce76103e7a87d8d8d64289e8f36a2e5612`
- Short: `C:\Dev\fib_pullback_short_dev.json`, SHA-256
  `e8f9b71974a5d83fbc6f7f83ef795383aa7edd0e4127b562eefc66775d89e5cd`

## Ruling

Do not run walk-forward, open the holdout, create a PAPER slot or optimize exits for this frozen version. Its gross
expectancy is already negative, so costs are not the primary cause. Any future revision must be a new preregistered
hypothesis with an economic change to entry selectivity (for example, structure/volume confirmation), not a parameter
search over Fibonacci ratios or a post-hoc symbol filter.

ROADMAP FIT: failed hypotheses remain in the strategy ledger and prevent repeated rediscovery.
DEFERRED: revised FIB-PULLBACK-02, PAPER activation, holdout, mainnet and non-Binance venues.
