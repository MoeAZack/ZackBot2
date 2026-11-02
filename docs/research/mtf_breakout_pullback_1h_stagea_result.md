# MBP-1H Stage-A result

Status: **REJECTED AFTER WALK-FORWARD / PARKED / NO PAPER AUTHORITY**

The frozen `mtf_breakout_pullback_1h` candidate was run once per side on CORE-20 development. Long failed immediately.
Short passed development unchanged, so only short opened the three preregistered chronological folds. It failed all
three. No parameter, side, symbol or management rule was changed after any result.

| Cell | Trades | Symbols | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Long development | 188 | 18 | +$7.36 | -$34.31 | -5.79R | -0.031R | 0.925 | 18.14R |
| Short development | 171 | 18 | +$140.91 | +$94.50 | +19.67R | +0.115R | 1.233 | 11.94R |
| Short WF1 | 142 | 18 | -$33.99 | -$70.62 | -13.87R | -0.098R | 0.793 | 28.55R |
| Short WF2 | 218 | 20 | +$36.45 | -$21.55 | -1.81R | -0.008R | 0.959 | 25.44R |
| Short WF3 | 155 | 20 | -$84.59 | -$127.60 | -28.26R | -0.182R | 0.651 | 32.39R |

The short development edge did not generalize: zero of three forward folds was profitable after costs, and two were
negative before costs. The candidate therefore fails the frozen two-of-three fold gate and is parked. The strong
development cell cannot be used to revise the rule or select a favorable period.

Packets are under `C:\Dev\ZackBot_cowork_data\mbp1h_stagea\`; embedded SHA-256 values:

- long development: `50986a406246edfdfdbf70e84d7844891ddb7dcc9d6d7b2a0c131b9722ee255c`
- short development: `7b0518a880fe1cb764e8dd9bf971e56fc9d1d47720685bd2a66fd3301e2e3edf`
- short WF1: `900b42fdebd9dab795badaa33d4f77d174d9f2e8fac9351c82ac3a14ded0919a`
- short WF2: `7dbe1685390a87de2e70968e614017263b22b71b82880213e13f8fa0319296a3`
- short WF3: `e6ddddba8c6b1a1989be93cd4695fe5d4aef31d059f9f35a34c4dd9642167f9b`

ROADMAP FIT: lower-turnover, larger-move candidate with genuine chronological falsification.

DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and post-result rescue.
