# Candidate 114 Chande Forecast Oscillator Stage-A result

Verdict: **REJECT / BLOCK**. Research-only and default-OFF; no PAPER, runtime, fold, or holdout action.

- Branch: `codex/cfo-c114`.
- Frozen head: `3bf7b00d66ad98055f2cec5631f0f94b691d1f18`; tree `476e9cdd7d4ab61a898e81ebfd302f2c07b0d082`.
- Preregistration SHA-256: `1c059ef12c3641100c2810aa02c961aa94df3f6e46267a62821d7fcbda0acda3`.
- Artifact: `C:\Dev\ZackBot_cowork_data\cfo_c114\cfo-long-dev.json`.
- Artifact SHA-256: `37c9c2093a0a52089bcc81ed7e8d071ceabdcd500ecd7238db22bd0d8a39adca`.
- Canonical packet SHA-256 (independently reproduced): `7cb3697653abe17bee94271ae33187cfc4ddeff403b687875f7f649cd8e7e1d9`.
- Funding manifest digest: `54912d9d2bf6e6fc45bc75c0553f0bd867e877972beeb3452785937379baf1e7`.

The frozen CORE-20 development run closed 2,412 long trades over all 18 symbols: 1,556 above and 856 below the BTC
4h EMA200 regime boundary. Pre-cost gross was -$23.271324. Fees were $323.056163 and actual signed funding was
$10.793505, leaving net P&L -$357.120991, net -111.042368R (mean -0.046037R), PF 0.827571, and max drawdown
119.403812R.

Passed gates: sample >=100, symbols >=8, and >=20 closes in each regime. Failed gates: pre-cost gross positive, mean
net R positive, PF >1.05, and max drawdown <=15R. Stage-A PASS is false and isolated PAPER eligibility remains BLOCK.
The rolling-regression endpoint residual has no edge before costs and fails payoff/risk materially; no lookback,
threshold, filter, or exit alteration can rescue this cell. No parameter tuning is authorized.

Focused tests: 6/6 before freeze and 6/6 after result recording. The sealed holdout was not accessed.
