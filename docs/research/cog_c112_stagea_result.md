# Candidate 112 Center-of-Gravity zero-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/cog-c112`.
- Frozen head: `787af8fe64df7d834228f794a8637894d73f6ee8`; tree `cdd4274c89a813a269fb0eeec7f7c7f2d558496d`.
- Preregistration SHA-256: `a2823a35001c586044fa17c6c52baba7f7d7ac49eb74538bb8acc3f6809c552e`.
- Artifact: `C:\Dev\ZackBot_cowork_data\cog_c112\cog-long-dev.json`.
- Artifact SHA-256: `c89f8713f298c6d7e54ea91134e3b38c72ec5e31dbf3a437aab7fe32578c8d94`.
- Canonical packet SHA-256 (independently reproduced): `8b19a8ada3abba05dd85eb6488e473ff9ed5674dbd7f7d02806cfcfc3eeaf94a`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 1,056 long trades over all 18 symbols: 645 above and 411 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$25.168938. Fees were $180.953922 and actual signed funding was
$13.736423, leaving net P&L -$219.859284, net -45.649394R (mean -0.043229R), PF 0.888198, and max drawdown
76.650857R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The temporal median-price balance transition has no edge before costs and fails payoff/risk materially; no lookback,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
