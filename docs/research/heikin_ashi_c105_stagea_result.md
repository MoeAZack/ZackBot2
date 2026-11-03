# Candidate 105 Heikin-Ashi state-flip Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/heikin-ashi-c105`.
- Frozen head: `5d61860cbbae45e7708528cf6dd9e89b40b29835`; tree `8d59bdf91555e7a9ad5af33876b2fb95baa7951a`.
- Preregistration SHA-256: `e859e6489304c061e0cf801b473b90af99726731499c631616ab055476fe5297`.
- Artifact: `C:\Dev\ZackBot_cowork_data\heikin_ashi_c105\heikin-ashi-long-dev.json`.
- Artifact SHA-256: `32aabbc31e34c26954fbdb7134628376a674ad45d43371b64a4944d8bddbe3c1`.
- Canonical packet SHA-256 (independently reproduced): `37ccb73227e2cd9475271ffb6fa40d8c20fc0b2b8a1eeb3b4be850a9ebb92595`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 2,254 long trades over all 18 symbols: 1,417 above and 837 below the BTC
4h EMA200 regime boundary. Pre-cost gross was +$41.194221. Fees were $351.161291 and actual signed funding was
$14.642232, leaving net P&L -$324.609301, net -89.528340R (mean -0.039720R), PF 0.862159, and max drawdown
113.865405R.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The recursive transformed-candle flip is too turnover-heavy to survive the frozen cost model and fails payoff/risk
materially; no state definition, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
