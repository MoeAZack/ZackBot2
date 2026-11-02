# VER-15 development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `vol_expansion_retest_15m` candidate was run once per side on CORE-10 for
`2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Neither side passes the preregistered development gate, so no forward fold
was opened and no rule was tuned after the result.

| Side | Trades | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Long | 34 | -$34.57 | -$50.43 | -10.54R | -0.310R | 0.345 | 12.91R | reject: economic and sample gates |
| Short | 30 | +$18.51 | +$3.99 | +0.88R | +0.029R | 1.089 | 2.66R | reject: fewer than 60 trades |

The short cell is directionally interesting but statistically too sparse for the frozen Stage-A gate. Enlarging its
symbols, relaxing confirmation, or opening the forward folds after seeing this result would be post-result selection.
Any materially different retest hypothesis must be counted and preregistered as a new candidate.

Packets (outside the repository):

- `C:\Dev\ZackBot_cowork_data\ver15_stagea\ver15-long-dev.json`, embedded packet SHA-256
  `f85232cedfac8aa574c8562bc59e7abc8cab3dd03d273061e77fdbe1a191a8ba`
- `C:\Dev\ZackBot_cowork_data\ver15_stagea\ver15-short-dev.json`, embedded packet SHA-256
  `f82be2c509bfd6cf8d4afdc06b175655c48e7b626ad4e07a861e6ae99223da5e`

ROADMAP FIT: continuous 15-minute candidate conveyor; recorded rejection without consuming PAPER capacity.

DEFERRED: chronological folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet and parameter rescue.
