# RAS-1H Stage-A result

Status: **REJECTED AFTER WALK-FORWARD / PARKED / NO PAPER AUTHORITY**

The frozen `regime_adaptive_switch_1h` candidate was run once per side on CORE-20 development. Long failed immediately.
Short passed development unchanged and therefore opened the three preregistered chronological folds. All three failed.
No state threshold, sub-rule, symbol, side or management value was changed after any result.

| Cell | Trades | Symbols | Gross P&L | Net P&L | Net R | Mean R | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Long development | 346 | 18 | -$73.87 | -$148.20 | -32.56R | -0.094R | 0.804 | 57.21R |
| Short development | 414 | 18 | +$164.35 | +$64.03 | +16.13R | +0.039R | 1.075 | 25.57R |
| Short WF1 | 374 | 18 | -$88.67 | -$178.05 | -41.32R | -0.110R | 0.733 | 48.42R |
| Short WF2 | 455 | 20 | -$59.19 | -$149.56 | -30.94R | -0.068R | 0.813 | 52.61R |
| Short WF3 | 373 | 20 | -$68.71 | -$179.37 | -41.85R | -0.112R | 0.752 | 46.35R |

The short development edge did not generalize: zero of three forward folds was positive, all were negative before costs,
and drawdowns breached the frozen limit. The family is parked. Selecting only one mode or changing the 4h state after
seeing these results would be post-result rescue.

Packets are under `C:\Dev\ZackBot_cowork_data\ras1h_stagea\`; embedded SHA-256 values:

- long development: `c0d8d4013fcd14d27c25e5b8df91dcd31a67b05368f027e347b81474fa35b3bb`
- short development: `acec66ebfe674e255ad6010a4359498abb2606e52ee2d5c2098c76a85167bc73`
- short WF1: `38dd3418e6b929e5a5c16e9409b9e011e63cee1a0ab0e38b650043e0ca5bd571`
- short WF2: `c58c0a23e765fb7e539fc0d68a1717257039cb1a43aed455306202dec045e3d3`
- short WF3: `e5c15aff9aa0e617c7cec87942cbe017f998a4eda7a8b889915b591fac0ed998`

ROADMAP FIT: causal regime-adaptive 1h candidate with genuine chronological falsification.

DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and post-result rescue.
