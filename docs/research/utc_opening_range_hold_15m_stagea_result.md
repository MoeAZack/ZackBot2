# ORH-15 development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `utc_opening_range_hold_15m` candidate was run once per side on CORE-10 for
`2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. It achieves the intended higher frequency, but both sides fail the
preregistered after-cost gate. No forward fold was opened and no rule was changed after inspection.

| Side | Trades | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Long | 545 | -$58.74 | -$283.25 | -80.39R | -0.147R | 0.726 | 94.22R |
| Short | 507 | +$34.43 | -$164.66 | -36.48R | -0.072R | 0.818 | 51.60R |

The short side is modestly positive before costs, but $197.46 of fees plus funding overwhelms that gross edge. The
long side is negative before costs. This is direct evidence that restoring turnover without a sufficiently large edge
only accelerates cost drag. Threshold or management rescue on this interval is forbidden.

Packets (outside the repository):

- `C:\Dev\ZackBot_cowork_data\orh15_stagea\orh15-long-dev.json`, embedded packet SHA-256
  `502d58e1b226a9e4a67271573255d98ec769d3767fe75c006083025495db544e`
- `C:\Dev\ZackBot_cowork_data\orh15_stagea\orh15-short-dev.json`, embedded packet SHA-256
  `dbf7f30710ff022af657a3f91e11888c0510066704bb562012819438f204eeb9`

ROADMAP FIT: higher-frequency 15-minute conveyor screen with realistic cost rejection.

DEFERRED: chronological folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet and post-result rescue.
