# Candidate 106 Fisher-transform zero-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/fisher-transform-c106`.
- Frozen head: `7e5b1ee7231d03b5990baad93ccb751bbb03ddfe`; tree `4cd2eacb72ae526b981c8ab0399daac3ec134f5b`.
- Preregistration SHA-256: `6cdd5ba9c6e6c74ae245f80dd605fcb2084f397311942d983009e2ac5eb61704`.
- Artifact: `C:\Dev\ZackBot_cowork_data\fisher_transform_c106\fisher-long-dev.json`.
- Artifact SHA-256: `128357364479d094bec9bcb61946e5c3619ff9277e5f8ff8a2c2153db6b0b618`.
- Canonical packet SHA-256 (independently reproduced): `297b991c8c91d2116657c8d1fa1543328ea4b7e1ce49a0c85b4f48c7619a91bf`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 892 long trades over all 18 symbols: 547 above and 345 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$60.524473. Fees were $150.435738 and actual signed funding was
$14.963986, leaving net P&L -$225.924196, net -47.656616R (mean -0.053427R), PF 0.869761, and max drawdown
83.157492R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The recursively Gaussianized price-location transition has no edge before costs and fails payoff/risk materially; no
lookback, threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
