# Candidate 100 DeMarker oversold-recovery Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/demarker-c100`.
- Frozen head: `e738327156e82a6ad8046a27e59a836657f0ffe6`; tree `7060ae20ed4e81920ee97e422dad266fc0751268`.
- Preregistration SHA-256: `95604dc4c2f611d6cf9d48c557ba38b8dd9d17b44ba76bd5db9af6c16a6caa87`.
- Artifact: `C:\Dev\ZackBot_cowork_data\demarker_c100\demarker-long-dev.json`.
- Artifact SHA-256: `3297c2cb5e8326fac87380227e0eb53ed9f5178b1802372841ee55ddd99544f9`.
- Canonical packet SHA-256 (independently reproduced): `ad7ae70fe1d727b953388d484dce0bb9249ae121a398287a3ba2aeb1f32671e8`.

The frozen CORE-20 development run closed 551 long trades over all 18 symbols: 336 above and 215 below the BTC 4h
EMA200 regime boundary. Pre-cost gross was -$36.698533. Fees were $94.049340 and actual signed funding was $6.515194,
leaving net P&L -$137.263068, net -27.116410R (mean -0.049213R), PF 0.864933, and max drawdown 35.037206R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The frozen incremental-extrema recovery has no edge before costs and payoff/risk fail materially; no DeMarker horizon,
boundary, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
