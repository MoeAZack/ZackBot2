# Candidate 91 Corwin-Schultz implied-spread Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/implied-spread-c91`.
- Frozen head: `4385ee3c20b34294b80f3a739c4972fcb7a4a1ef`; tree `c282d38c6dd1ac871fafca134bc9ee578f98fb8f`.
- Preregistration SHA-256: `7d8b0180068c548ad8105e1d08f8723b07ba0b09ee48f259aa3cbd9e1e9eaf81`.
- Artifact: `C:\Dev\ZackBot_cowork_data\implied_spread_c91\implied-spread-long-dev.json`.
- Artifact SHA-256: `e8bf5d88ad67c76254d0f492ebcf68eaa2e1bf85f8e9024e60441ada07538628`.
- Canonical packet SHA-256 (independently reproduced): `248893530df15ce1f4e2fe14c4319b8afdb52ca1d4f460e5fc01dcf36d4d4145`.

The frozen CORE-20 development run closed 1,139 long trades over all 18 symbols: 711 above and 428 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$23.454039. Fees were $226.409516 and actual signed funding was $21.258858,
leaving net P&L -$271.122413, net -60.384706R (mean -0.053016R), PF 0.876237, and max drawdown 89.408202R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The hypothesis is negative before costs, so neither cost attribution nor a liquidity-threshold change can rescue this
frozen cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed post-2026-09-28 holdout was not accessed.
