# Candidate 120 Hurst rescaled-range persistence Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/hurst-rs-c120`.
- Frozen head: `1316480d3036d87c4eb6ccbd6e34f7797c52c352`; tree `2ba3bbd8058209480923d855ed78bf158b74ad1a`.
- Preregistration SHA-256: `dfc939553ee2502590db4580716612c75aba1b150304c5c6b8a3c077e4015cdc`.
- Artifact: `C:\Dev\ZackBot_cowork_data\hurst_rs_c120\hurst-long-dev.json`.
- Artifact SHA-256: `702bad6f6cd18598d21b04f9d766b504fff41a6f371fcbe66b49317eafe2ff49`.
- Canonical packet SHA-256 (independently reproduced): `ef9f5d0bf5adf41b0a48f263b0c114215ff8c2940b63831e383966deb73f96dc`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 980 long trades over all 18 symbols: 675 above and 305 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$117.502529. Fees were $157.657061 and actual signed funding was
$12.931072, leaving net P&L -$288.090662, net -75.545349R (mean -0.077087R), PF 0.823587, and max drawdown
117.936824R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The multi-scale rescaled-range persistence transition has no edge before costs and fails payoff/risk materially; no
window, scale, threshold, drift filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
