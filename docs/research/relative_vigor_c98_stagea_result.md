# Candidate 98 Relative Vigor bullish-cross Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/relative-vigor-c98`.
- Frozen head: `ea44c23520db6580beaaa60c3fc1fc102e2abbe4`; tree `e69f5975ab60036d9bb072f59429f3f16245bd75`.
- Preregistration SHA-256: `8d60129694aa00484f698213e558b0429357ddc841f2b4f196993eed5dc74f2d`.
- Artifact: `C:\Dev\ZackBot_cowork_data\relative_vigor_c98\relative-vigor-long-dev.json`.
- Artifact SHA-256: `f39d838a63d073e3bae9bafe9e06d4a0ea69da44071c6e99bfd6113182e84e54`.
- Canonical packet SHA-256 (independently reproduced): `4627994b5706f1ac19b68a5479e8f43bd2fcdbd44e352c930c6b3ab861ed8bd2`.

The frozen CORE-20 development run closed 1,296 long trades over all 18 symbols: 823 above and 473 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$68.060444. Fees were $252.108593 and actual signed funding was $9.438579,
leaving net P&L -$193.486728, net -42.035158R (mean -0.032435R), PF 0.892579, and max drawdown 78.207382R.

Passed gates: pre-cost gross positive, sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The small gross edge is overwhelmed by turnover costs and payoff quality/risk fail materially; no vigor horizon,
smoothing, threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
