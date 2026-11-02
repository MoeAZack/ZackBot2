# VRC-15 development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `vwap_reclaim_15m` candidate was run once per side on CORE-10 for the preregistered development interval
`2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Both sides fail the immediate development gate. No threshold, symbol,
regime, or management value was changed after reading the result, and the chronological forward folds were not run.

| Side | Trades | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| Long | 602 | +$35.87 | -$217.93 | -54.03R | -0.090R | 0.801 | 55.11R |
| Short | 590 | -$86.06 | -$292.99 | -84.48R | -0.143R | 0.685 | 90.17R |

The long side had a small positive pre-cost gross result, but $251.02 of fees plus funding erased it. The short side was
negative before costs. This is enough to reject both hypotheses under the frozen rule; further tuning on this interval
would be holdout-style data mining.

Packets (outside the repository):

- `C:\Dev\ZackBot_cowork_data\vrc15_stagea\vrc15-long-dev.json`, embedded packet SHA-256
  `3f6a887786c2c7d5a950519ba815b40ef47476f9c66d7d4f00b14cb450e5e78c`
- `C:\Dev\ZackBot_cowork_data\vrc15_stagea\vrc15-short-dev.json`, embedded packet SHA-256
  `051d6b79afee2ac6e97e971306c4b6b438defbf07986651f395d90ba2b92277a`

ROADMAP FIT: continuous 15-minute candidate conveyor; a fast rejection is useful evidence and frees the lane.

DEFERRED: forward folds, execution-grade Stage B, PAPER/testnet, runtime settings, mainnet, and parameter rescue.
