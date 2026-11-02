# TLP-15 development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `trend_liquidity_pullback_15m` candidate was run once per side on CORE-10 for
`2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Both sides fail the preregistered economic and minimum-frequency gates.
No forward fold was opened and no rule was changed after inspection.

| Side | Trades | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Long | 10 | -$30.16 | -$34.57 | -7.09R | -0.709R | 0.202 | 7.09R |
| Short | 7 | -$13.01 | -$16.12 | -3.25R | -0.465R | 0.261 | 3.68R |

The strict strength/liquidity conjunction is both sparse and negative before costs. Relaxing ADX, DMI, participation,
pullback or trend thresholds after seeing this result is forbidden rescue. A materially different hypothesis must be a
new counted preregistration.

Packets (outside the repository):

- `C:\Dev\ZackBot_cowork_data\tlp15_stagea\tlp15-long-dev.json`, embedded packet SHA-256
  `64942493a02fa328aa337931a9fb0e0de83199145e2413f9f40497a7afb10d58`
- `C:\Dev\ZackBot_cowork_data\tlp15_stagea\tlp15-short-dev.json`, embedded packet SHA-256
  `b499077e756f97ccfe17a20a4de3f4d859425e29506f5895fedaa9a4e16a4ad5`

ROADMAP FIT: continuous 15-minute strategy conveyor and rapid rejection of non-economic candidates.

DEFERRED: chronological folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet and threshold rescue.
