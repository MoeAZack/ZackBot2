# Candidate 119 Correlation Trend Indicator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/cti-c119`.
- Frozen head: `2c2a43ea3a86186da52cb053f446052e5f53aeac`; tree `c288ac298bca1354d3d2fe4a880520d17330fbb4`.
- Preregistration SHA-256: `f77ff525ec392eeb5040a99c00a99d8d0452d4e8d8cfcd7d17f5136024c2e171`.
- Artifact: `C:\Dev\ZackBot_cowork_data\cti_c119\cti-long-dev.json`.
- Artifact SHA-256: `46c424f1f94bb5fd0d022275cb421a0574e1b71dec2e2acbf88104820468db92`.
- Canonical packet SHA-256 (independently reproduced): `c619f80f007866db9de59d487bdd87ac18bdffadc825df657efaf9bdc0f2cdf9`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 625 long trades over all 18 symbols: 383 above and 242 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was +$167.583976. Fees were $140.968897 and actual signed funding was
$14.786248, leaving net P&L +$11.828831, net +12.027478R (mean +0.019244R), PF 1.006286, and max drawdown
38.711152R.

Passed gates: pre-cost gross positive, mean net R positive, sample >=100, symbols >=8, and >=20 closes in each regime.
Failed gates: PF >1.05 and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The price-time alignment signal is profitable but insufficiently robust on payoff and risk; no window, threshold, filter,
or exit alteration can rescue this frozen cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
