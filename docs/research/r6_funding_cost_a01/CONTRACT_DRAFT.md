# R6-FUND-COST-A01 — preregistration draft

Status: DRAFT, before funding outcomes. No funding data has been requested or inspected for this study. Freeze this document and record its content hash, the frozen tape hash, and the exact data and code revisions before Phase A. Any subsequent material change requires a separately identified amendment made before examining affected outcomes.

## Question and fixed scope

On the frozen R6 venue163 order-zero tape of 150 closed lots, how does the charged unsigned 1 bp per 8 hours funding proxy compare with historical signed funding cash computed at each event's mark price? Input: `F:/ZackBot2_data/C1H-20261112/review-branch/docs/research/codex_regime_flip_review_20261114/fit163/order0_own_trades.csv`; use accepted native confirmation PR #349 at head `60c0a1c5` as the comparison context. The terminal open lot is explicitly excluded. This is fixed-tape cost sensitivity, not a new backtest, admission test, alpha selection, holdout, or equity-return estimate. Freeze symbols, entries, exits, quantities, fee, slippage, and all other fills and costs; never feed adjusted cash back into sizing or execution. Report in the tape's cash units and, if desired, its existing 8-unit risk convention, explicitly not account returns.

## Baseline gate

Before querying funding, reconcile the frozen tape's recorded per-lot `funding`, `pnl`, `pnl7`, `pnl15`, and `pnl23` against the accepted independent replay and its pinned inputs. Independently sum the original proxy for every managed four-hour bar from entry through exit inclusive: `qty * bar_close * 0.00005` per bar, charged regardless of side. Confirm symbol, side, timestamps, quantity, entry, exit, and reason match the frozen rows. Reconcile per-lot and totals within a predeclared absolute floating-point tolerance of 1e-7 cash units; document input hashes and differences. A failed or unverifiable baseline stops the study; do not proceed or silently repair the tape.

## Event accounting and uncertainty

For each funding event with a verified rate and mark, actual cash is `-side * qty * fundingRate * markPrice`, where long is +1, short is -1, rate is a signed decimal, quantity is the frozen base-asset quantity, and mark is quote per base unit. Replace the charged proxy by adding its positive recorded `funding` back to each fixed-tape P&L and adding signed actual funding cash. Apply the same adjustment to each existing cost scenario; leave all other cash flows unchanged.

The tape's timestamps are UTC bar-open timestamps. Entry is after the entry bar opens: an event exactly at `t_in` is excluded. Events strictly after `t_in` and strictly before `t_out` are guaranteed held. For signal/time exits, the exit is at the close of the `t_out` bar, four hours after `t_out`: events at `t_out` and strictly before that close are guaranteed held; an event exactly at that close is ambiguous. For stop exits, actual execution can occur anywhere within the `t_out` bar, including near its open: events from `t_out` through that bar's close, inclusive, are ambiguous. Do not impute an intrabar stop time, including for an apparent gap.

For each lot report guaranteed-event cash and conservative bounds: add `min(0, event_cash)` for each ambiguous event to the lower bound, and `max(0, event_cash)` to the upper bound. Preserve event-level decisions and list all ambiguous events. This is an envelope, not a claim that every combination of inclusion is physically attainable. If no ambiguity remains, bounds coincide. Entry-boundary exclusion is a declared latency assumption, not a timestamp-level observation.

## Data acquisition, completeness and checks

Use Binance USDⓈ-M Futures public `GET /fapi/v1/fundingRate`, with no authentication. Record symbol, requested inclusive `startTime` and `endTime`, response metadata, raw event timestamp, signed `fundingRate`, `markPrice`, and raw response hash; UTC milliseconds are the source clock. Query ascending with `limit=1000`, advancing the next `startTime` to the last returned timestamp plus 1 ms; stop at the inclusive end. Check ordering, duplicate keys, overlap, cursor advancement, response errors and rate limits. Do not treat a nonempty response or successful pagination alone as proof of complete event coverage.

Phase A is strictly at most six deterministic HTTP requests: select the first three alphabetical symbols that traded on the frozen tape, and for each request the inclusive held-time span of its first and last closed lots in chronological entry order (ties by tape row order). One request per selected span; do not paginate if a response reaches the limit or any query fails. Record observed scheduling, units, mark availability, and whether an authoritative coverage/schedule check is possible. Phase A makes no performance or selection decision. If a query needs another page, schedule is uncertain, or endpoint/metadata does not support completeness, document the limitation and stop or amend before full coverage.

For full coverage, predefine and verify expected historical event schedules and instrument-active intervals from authoritative evidence, rather than assuming a fixed eight-hour cadence across time. Each held window must have verifiable complete coverage including its ambiguous boundary interval. A missing event, rate, mark, listing/interval evidence, pagination page, or unresolved coverage is `INCOMPLETE`, never zero funding. Exclude no failed lot from an aggregate: label the aggregate incomplete until all required events and bounds are resolved.

## Predeclared tests and outputs

Use deterministic synthetic tests for: positive and negative rates on both long and short positions; decimal rate and price/quantity units; proxy removal and no change to fees or fills; exact entry, exit-open and exit-close boundaries; stop-event lower/upper bounds with positive and negative cash; empty/incomplete pages, inclusive endpoints, duplicate events, 1000-item pagination, cursor advancement and missing mark/rate. Assert unchanged tape fields and compare per-lot cash to hand-calculated examples before using actual data.

If complete, report baseline proxy cash, guaranteed actual funding cash, ambiguous-event bounds, replacement delta and fixed-tape P&L intervals, by lot and aggregate. Report event counts and completeness evidence after retrieval, as well as limitations and any deviations. Do not report an equity curve, infer profitability for a wider universe, tune strategy parameters, or authorize runtime/PAPER actions from this study.
