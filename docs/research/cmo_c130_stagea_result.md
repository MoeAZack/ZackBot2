# Candidate 130 Chande Momentum Oscillator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/cmo-c130`.
- Frozen head: `2cb0a38803663d2257c1cbe33d834689d6188b53`; tree `797e2498994cb09e39ba588cd58c764bda8cc733`.
- Preregistration SHA-256: `c29bc25fd5db42d149ae810d5f9a8026f96c4ab4fc6d499bcc502b38562873d6`.
- Artifact: `C:\Dev\ZackBot_cowork_data\cmo_c130\cmo-long-dev.json`.
- Artifact SHA-256: `abe846188c86c6004df91e8d5eb1ea5eaddc03360b3631bbccbc1481f41fe0b1`.
- Canonical packet SHA-256 (independently reproduced): `ef8f85222fda027d2f2dc5d645640a2f0abd64cad1b6e957552afd392891fd44`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,461 long trades over all 18 symbols: 898 above and 563 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$82.494160. Fees were $210.214659 and actual signed funding was $14.255768,
leaving net P&L -$306.964588, net -79.996515R (mean -0.054755R), PF 0.831126, and max drawdown 87.446710R. Exit
counts were 1,128 signal, 142 stop, 19 time, and 172 target.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen fixed-window momentum-balance zero-cross has no edge before costs and fails payoff/risk materially; no window,
filter, threshold, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 7/7 before freeze and 7/7 after result recording. The sealed holdout was not accessed.
