# Candidate 92 robust median-MAD location Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/robust-location-c92`.
- Frozen head: `c2fc56868341a34eb9aadb3058800a7975b7cdc7`; tree `7d023775f42e15c09f679ce2abe6a7436a167206`.
- Preregistration SHA-256: `72ddab764b512a2701a39fc8fe71526c8c5187dc6928e6d15a1774b4498404f3`.
- Artifact: `C:\Dev\ZackBot_cowork_data\robust_location_c92\robust-location-long-dev.json`.
- Artifact SHA-256: `17a52ce1ce1ce9d89a01b21b260369ec5023140c8befe767f04096503306a3d9`.
- Canonical packet SHA-256 (independently reproduced): `c661f08d818730981bc3283fc28aa7c20c15cc14f7be1922d0138891fe212e79`.

The frozen CORE-20 development run closed 402 long trades over all 18 symbols: 202 above and 200 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$52.178399. Fees were $70.242038 and actual signed funding was $1.722175,
leaving net P&L -$124.142613, net -23.924656R (mean -0.059514R), PF 0.848811, and max drawdown 39.068683R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The hypothesis is negative before costs; no robust-z threshold or exit alteration can rescue this frozen cell. No
parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed post-2026-09-28 holdout was not accessed.
