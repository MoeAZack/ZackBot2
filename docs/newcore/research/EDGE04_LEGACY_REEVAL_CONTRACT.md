# EDGE-04 — finite legacy-strategy re-evaluation

Status: **frozen before any EDGE-04 market result, 26 October 2026 Cairo**. The machine-readable authority is
`EDGE04_LEGACY_REEVAL_PREREG_v1.json`. This pass changes no strategy, PAPER setting or runtime behavior.

## Question and fixed cells

EDGE-04 asks whether four pre-existing 4h runtime hypotheses survive one common causal, execution-grade protocol:

| ID | Frozen cell | Important distinction |
|---|---|---|
| H1 | `pullback_rsi` SHORT, `when=bear` | Existing short signal and management; only the shared causal BTC bear admission is added. |
| H2 | `rotation` LONG, `k=5` | Cross-sectional rank is recomputed only from the synchronized book available at that close. |
| H3 | `exp_trend` LONG | Same entries as H4, but the runtime 3R target and 180-bar cap remain part of H3. |
| H4 | `ema_mom` LONG | Exact runtime signal and stop-only management; H3 and H4 are not pooled. |

Every decision is made on a closed candle and fills at the next executable open. Signal details and management are
bound to `strategies.py` at `07cbc8172de4d05b01532f1797d217000eed0fa9`; its SHA-256 is recorded in the prereg.

## Finite budget

There are seven strategy variants: four primaries and three diagnostics (`H1 when=any`, H2 `k=3`, H2 `k=7`). There
are exactly two baselines per primary, for 15 append-only trial records total. Stress rows reprice the same episodes
and do not create selectable variants. No diagnostic, stress result or opposite hypothesis may replace a failed
primary. The holdout family is the four primaries, Holm-corrected at alpha 0.05.

The prior-look inventory is frozen before execution. It contains 18 uniquely identified candidate packets, the 18
registered `trend_ema_mom` trials, and 32 named legacy CSV rows. The root `results_single.csv` copy and the c8c7b92
parity reruns are deduplicated. Partial CORE-20 packets remain prior looks but are superseded as evidence by the fuller
packets. Because old CSVs lack immutable run/config/data identities, their 32 rows are conservatively counted rather
than guessed into fewer trials. EDGE-04 therefore reports search-aware statistics with 75 historical-plus-new strategy
looks; this does not relax the four-primary Holm gate.

## Data, chronology and costs

The headline is frozen CORE-10-ELIGIBLE-9. HYPE is an available-period sensitivity only. Train and the five annual
walk-forward folds are the already committed R4 split boundaries, ending exclusively at 28 September 2026. Books,
capital, positions and indicators reset at every boundary. The sealed holdout is 2 November 2026 through 1 November
2027 UTC, revealed once only after 30 episodes per primary.

Base execution uses 5 bp taker fees per fill, the 5 bp unfitted slippage fallback, actual signed funding with a causal
mark, historical exchange filters and conservative gap/stop ordering. Fixed stresses are 1.5x all variable costs, 2x
fees/slippage, 2x funding, 5x gap slippage and 60-second entry latency. Results from the legacy candidate runner alone
are non-promotable because it uses flat funding and legacy filters.

## Pass rule

A primary passes only if every machine-readable gate passes: completeness, at least 60 episodes and 0.75/week,
positive net R in at least four of five walk-forward folds, combined PF above 1.10, positive episode-clustered 95%
lower bound, positive gross edge, both baselines beaten, 1.5x costs still positive, no 60-second sign flip, symbol
concentration within 35%/60%, base equity drawdown below 25%, closed-R drawdown below 25R and 1.5x-cost drawdown below
35%. Survivors remain low-frequency research candidates until the separate holdout, shadow and PAPER gates.

## Implementation boundary and blockers

Reuse the current StaticDataset/PIT access, SplitPlan, cost model, exchange-rule snapshots, ledger and immutable report
envelopes. Before a market result, add and independently review only the adapter that feeds the exact runtime signals
and the synchronized rotation context into that execution path. The current candidate runner is insufficient for an
execution-grade verdict, and the PIT runner does not yet expose those four runtime keys or H2 rank context.

ROADMAP FIT: Binance strategy discovery and trustworthy historical validation.

DEFERRED: result execution, sealed holdout access, PAPER changes, runtime changes, mainnet, other venues and new
strategy families.
