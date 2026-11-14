# LONGSCOUT-V5 targeted independent review

ROADMAP FIT: bounded strategy-economics evidence for the active Binance/PAPER conveyor. DEFERRED: implementation, activation, account changes, tuning and further candidates. These are consumed-calendar diagnostics; neither failed candidate is rescued or eligible for activation.

GitHub PR #347 was observed at `eb5f615c78660490e76cad97b8f09c2fffbe1e89`; requested evidence head `f9a914babc53dd2c2ad1eac438a33c26fcdc0b88` is its ancestor. The V5 result, strategy and case paths are unchanged between these heads. Candidate source is `d88221334d7b6a2cc32141406e77146ee553ca85`, frozen cases `88fab578abdeaf48b39724c88b5b0fbe85c4c62d`. Review compared the requested historical head. The PR may have advanced since observation.

## Signal and cash evidence

All 166 input files (165 traded coins plus BTC context) matched their pinned manifests. Independent pandas code, importing neither submitted strategy nor research runner, rebuilt the rules, indicators and decision-time D01 eligibility:

| Candidate | Raw published / independently rebuilt | Eligible | Missing / extra |
|---|---:|---:|---:|
| High-beta BTC-down short | 1,696 / 1,696 | 978 / 978 | 0 / 0 |
| Ichimoku cloud | 41,258 / 41,258 | 24,723 / 24,723 | 0 / 0 |

The first five chronological order-zero closed trades in each case were independently reconstructed from candles and current-rule snapshot, including quantity flooring, stop/target or exit, fees, slippage, funding proxy and 15 bp repricing. All ten exit times and reasons matched, and P&L differed by at most $1.78e-15. This sample is deliberately small: it does not prove full admission sequencing, all exits, 40-order cash simulation, historical exchange filters, signed funding or live execution parity.

## Failed gates and causality

Independent arithmetic over the existing 40 published path-trade files reproduces the conclusions. High-beta has median 57 trades and +0.025536 R15, but fails minimum trade count, earlier half, ex-best coin, fresh134 subset and order-zero 30 closes in each half (21 early / 36 late). The result JSON records the latter failure correctly; the published V5 README's failure list omits it. Ichimoku has median 608.5 trades and +0.013887 R15 but fails 23 bp, late half, ex-best coin and fresh134 subset. The 31-coin subgroup does not justify dropping failed gates or selecting favorable coins.

Code inspection found no lookahead in the examined entry rules: BTC regime maps only completed UTC days to each decision-bar close; 180-bar beta and EMA use observations through the completed decision bar; the Ichimoku spans are shifted 26 bars into the past before comparison; native execution fills after signal at the next bar. This is a scoped analytical causality check, not a formal perturbation test or full engine review. Both studies use selected coins and an already-consumed calendar; fees, funding and exchange rules remain proxies.

Evidence: `independent_signals.py`, `independent_signal_results.json`, both independent signal CSVs, `independent_cash.py`, `independent_cash_sample.json`, `independent_gate_check.py`, and `independent_gate_arithmetic.json`. No account, key, runtime, scheduler or source changes were made.
