# XAV-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `xaccel_volume_1h` candidate was run once per side on CORE-20 for
`2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Neither side passes every preregistered development gate, so no
chronological fold was opened and no rule was changed after inspection.

| Side | Trades | Symbols | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Long | 270 | 18 | -$75.83 | -$129.90 | -28.90R | -0.107R | 0.733 | 31.98R |
| Short | 91 | 18 | +$19.80 | -$0.68 | +0.26R | +0.003R | 0.995 | 6.53R |

The short side is nearly flat after costs but fails both the >=100-trade floor and PF > 1.05. The long side is negative
before costs. Opening folds, relaxing volume, changing rank width, or promoting only selected symbols after seeing these
numbers would be post-result rescue and is forbidden.

Packets (outside the repository):

- `C:\Dev\ZackBot_cowork_data\xav1h_stagea\xav1h-long-dev.json`, packet SHA-256
  `c292efdc585ff27f7daaef87fbd1c9794f64e12781f2ef48e0a7ed6c9aafe4eb`
- `C:\Dev\ZackBot_cowork_data\xav1h_stagea\xav1h-short-dev.json`, packet SHA-256
  `4f5b08750adf2c518e9be40c328c6a5107d025c3a6380fee0ec324d4fa60b727`

ROADMAP FIT: selective lower-turnover candidate after five cost-dominated 15m screens.

DEFERRED: chronological folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet and post-result rescue.
