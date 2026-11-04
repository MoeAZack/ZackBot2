# Candidate 113 Relative Volatility Index Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/relative-volatility-c113`.
- Frozen head: `ea7b2a17c1e13ee3a20d5f2d05caa6c7a9cdfa6d`; tree `32af94a76583b546435831f8989356e2151f9dcc`.
- Preregistration SHA-256: `2aa669e304c3a31cffa392419e0792d917dc62f0786cdc366b8b33de72e1f7fe`.
- Artifact: `C:\Dev\ZackBot_cowork_data\relative_volatility_c113\relative-volatility-long-dev.json`.
- Artifact SHA-256: `b4709cecd3ee5aa3ebf9ef98f9d4f2e96a8b9ad5841ced63f23ef96fec6badbe`.
- Canonical packet SHA-256 (independently reproduced): `693227cea49cabee458834b8a461c6dcfa59a7046bbc83755b2afce1c81cef37`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,894 long trades over all 18 symbols: 1,130 above and 764 below the BTC
4h EMA200 regime boundary. Pre-cost gross was -$80.422293. Fees were $257.716226 and actual signed funding was
$12.833770, leaving net P&L -$350.972289, net -107.718048R (mean -0.056873R), PF 0.809764, and max drawdown
124.356192R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The directional-volatility balance has no edge before costs and fails payoff/risk materially; no standard-deviation
window, smoothing, threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
