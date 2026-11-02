# LSR-15 development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `liquidity_sweep_rejection_15m` candidate was run once per side on CORE-10 for
`2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Both sides clear the frequency floor but fail the preregistered economic
gate. No chronological fold was opened and no rule was changed after inspection.

| Side | Trades | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Long | 390 | -$181.22 | -$287.70 | -82.48R | -0.211R | 0.555 | 87.15R |
| Short | 289 | -$4.41 | -$116.64 | -25.27R | -0.087R | 0.788 | 29.73R |

The short cell is close to flat before costs but has no gross edge, and its $110.91 fees plus funding make the result
clearly negative. The long cell is materially negative before costs. Frequency, wick quality and cooldown therefore do
not rescue this hypothesis. Post-result threshold, side, symbol or management selection is forbidden.

Packets (outside the repository):

- `C:\Dev\ZackBot_cowork_data\lsr15_stagea\lsr15-long-dev.json`, embedded packet SHA-256
  `064021d3de7bbdbe4e8c0e7a7a8916ada2dba3003ce469ede9b36849235dd853`
- `C:\Dev\ZackBot_cowork_data\lsr15_stagea\lsr15-short-dev.json`, embedded packet SHA-256
  `d048de7be13b9ee949ade4fdb0116d7da31d338ce18848c96e315d13ae180fae`

ROADMAP FIT: fee-aware 15-minute liquidity-rejection screen with sufficient sample size.

DEFERRED: chronological folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet and post-result rescue.
