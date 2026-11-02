# DSR-1H development result

Status: **REJECTED / PARKED / NO PAPER AUTHORITY**

The frozen `dispersion_reversion_1h` candidate was run once per side on CORE-20 development. Long is modestly positive
but far below the preregistered 100-trade minimum; short has sufficient breadth but is economically negative. Neither
side may open chronological folds. No threshold, symbol, side or management rule was changed after inspection.

| Side | Trades | Symbols | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Long | 26 | 12 | +$9.11 | +$5.73 | +1.20R | +0.046R | 1.197 | 3.31R | reject: insufficient sample |
| Short | 102 | 15 | -$24.17 | -$37.69 | -7.61R | -0.075R | 0.729 | 8.41R | reject: economic gate |

The long result is not a development survivor because 26 observations cannot support the frozen claim. Relaxing the
dispersion or recovery threshold to increase frequency after seeing this outcome is forbidden rescue. The short side is
negative before costs and supplies no offsetting evidence.

Packets are under `C:\Dev\ZackBot_cowork_data\dsr1h_stagea\`; packet SHA-256 values:

- long development: `091b4f36486c0d77fea3ebe516fab84dc79913ea50f5e6d0db0039b77e8be3b3`
- short development: `b1ee571e0468b43f0dc477f8e6a701ef16ea4fa66098924e7710becec02339fe`

ROADMAP FIT: orthogonal BTC-neutral cross-asset reversion screen after trend-family failures.

DEFERRED: chronological folds, Stage B, holdout, PAPER/testnet, runtime settings, mainnet and post-result rescue.
