# Candidate 109 detrended-price zero-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/dpo-c109`.
- Frozen head: `7d35e54de62fb98a38c77959808044d001965490`; tree `3b66b70bcfe90cf244fb0331e2b7df866031c4d2`.
- Preregistration SHA-256: `4a33b135b503c02061a14dfb9577539802f76ff148d3ec95576d23e9e2dedc7e`.
- Artifact: `C:\Dev\ZackBot_cowork_data\dpo_c109\dpo-long-dev.json`.
- Artifact SHA-256: `c433383ae8986d12c9491ef03f9aa20a2bce5906f0cad8e2687368c03539525c`.
- Canonical packet SHA-256 (independently reproduced): `02c6fa1cfa7eba8bde8eb7441aa6d51ff7b91514b87cbeebcd3b9f0b1519a728`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,123 long trades over all 18 symbols: 681 above and 442 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$67.745828. Fees were $190.722029 and actual signed funding was
$13.745558, leaving net P&L -$272.213414, net -66.434237R (mean -0.059158R), PF 0.850895, and max drawdown
86.285356R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The lag-displaced price residual has no edge before costs and fails payoff/risk materially; no lookback, displacement,
filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
