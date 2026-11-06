# FACTORY-F02-EVAL-01

This is the frozen bridge from the already-running execution-grade **EDGE04 H3** evaluation to a possible isolated
`C-F02` PAPER cohort. It does not run another backtest and does not read the active result.

## Why this is not another strategy test

`C-F02` is `exp_trend` long on 4h. EDGE04 already names that exact candidate as H3 primary, with the same 2.5 ATR stop,
3R target and 180-bar cap. Starting a second market-data run would duplicate a preregistered trial and create another
selection opportunity. The final signed H3 output is therefore the only execution-grade decision input.

The older family packets remain context only: 171 closes and +56.692730R under legacy flat funding and filter
assumptions. They cannot promote the candidate or alter a gate.

## Frozen decision

PAPER eligibility requires the final immutable H3 primary to pass every preregistered EDGE04 gate: complete data and
accounting, enough activity, four positive walk-forward folds, positive cost-aware expectancy, B0/B1 superiority,
cost/latency stress, concentration, drawdown and Holm/search-aware multiplicity. Diagnostics and PAPER cannot rescue a
failed primary. An incomplete or unsigned result is `INCONCLUSIVE`, not a pass.

High drawdown is acceptable only inside the frozen numeric limits. It never waives a protection, data, accounting or
multiplicity failure.

## Later PAPER pack

Only after H3 eligibility and independent result-envelope evidence may Integration prepare a cohort pack. `C-F02` is
default OFF and runs alone; `C-F01` stays OFF for the entire cohort. The pack must freeze exact runtime/backtest parity,
measured costs, exchange filters, observation bounds, rollback and confirmed Binance testnet protection before enable.
PAPER is operational confirmation, not another tuning window.

This draft opens no market data, reads no EDGE04 result, changes no runtime/PAPER state and authorizes no activation.

Roadmap fit: continuous Strategy Factory conveyor. Deferred: result consumption, tuning, implementation, activation and
all mainnet/live work.
