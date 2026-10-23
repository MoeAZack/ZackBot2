# AI-SCAN-01 — causal AI chart scanner and calibrated shadow dashboard

## Roadmap fit and order

Phase 10 (ML in shadow), extending TA-SCOUT-01 and supporting EDGE-01. This is the ZackBot-owned version of an AI
chart scanner. It uses ZackBot/Binance causal data and does not install, clone or reverse-engineer TradingView.

AI-SCAN-01 is `NEXT` after the two accepted PR #187 primary packets land. Contract/schema and targeted-test preparation
may proceed in a separate worktree while they run, but no scanner work may modify those jobs, their packets or runtime
state, and no competing full suite runs until the jobs release resources.

## Owner-facing result

For each eligible symbol, timeframe and strategy family, the scanner shows:

- the deterministic `RULES` decision and the separate `AI SHADOW` opinion (`LONG`, `SHORT` or `NO_TRADE`);
- evidence coverage, disagreement, age and exact insufficient-data reason;
- `P(net_R > 0 after actual costs by the frozen terminal horizon)` only when the active calibration epoch has passed
  the frozen calibration gate below, otherwise `UNVERIFIED`;
- the causal entry zone, invalidation/stop and target plan copied from the immutable deterministic snapshot;
- estimated fees, funding, slippage and dollar/R risk from the measured execution model; and
- the later measured outcome, once available, compared with the original deterministic decision.

Probability, evidence coverage and model confidence are distinct fields. The UI must not collapse them into one
marketing-style percentage.

## Safety invariants

- PAPER/testnet and shadow-only. New UI/API surfaces default off until installed and verified.
- No order, settings, sizing, eligibility, stop, exit, rotation or strategy authority.
- Remote model analysis is queued and asynchronous; it cannot block a 15-minute cycle or position management.
- Inputs contain only values whose source/event timestamp is at or before the decision cutoff. Their receipt/ingest
  timestamp may be after that close, but must be no later than the recorded decision instant (`decided_at >= cutoff`);
  this permits ordinary exchange delivery latency without admitting information from a later market event. The snapshot
  must match the exact decision cutoff. A shadow result is display-fresh for `min(2 bars, 30 minutes)`, intentionally
  expiring mid-bar on 1h/4h views; later results remain historical evidence but are visibly stale. Missing, stale,
  malformed or insufficient inputs produce `INSUFFICIENT`, never an inferred score.
- Point-in-time inputs include universe membership, exchange-filter snapshots, funding data and every feature used by
  the decision. Restated or later-listed information cannot enter an earlier record.
- Current web/model knowledge cannot substitute for missing point-in-time news. Phase 11 `NewsProvider` remains separate.
- No credentials, exchange/order identifiers, arbitrary logs or raw private state enter a prompt.
- Model prose is untrusted and cannot modify the deterministic trade plan.
- Existing shared risk gateway remains the only possible future authority path; AI can never raise risk above 1x.

## Deliverables

1. **Immutable signal ledger.** One versioned, restart-idempotent record per candidate/decision with cutoff and ingest
   times, feature/schema hashes, strategy snapshot, deterministic decision, proposed plan, committee result,
   coverage/disagreement and a causally joined later outcome. Each model record pins the immutable provider model id,
   prompt-template hash, sampling parameters and raw-response hash. Changing any one opens a new calibration epoch;
   vendor aliases are not reproducible identifiers. Ambiguous joins fail closed.
2. **Scanner API and UI.** Sortable cross-coin table and per-signal detail. Labels clearly distinguish `RULES`,
   `AI SHADOW`, `UNVERIFIED` and `INSUFFICIENT`. With the feature off, the stable response is
   `{schema_version: 1, on: false, mode: "off", updated_at: null, rows: []}`.
3. **Evaluation harness.** Preregister B0 as the unchanged deterministic baseline and B1 as scanner ranking only.
   Evaluate exact runtime rules on anchored walk-forward and an untouched holdout with actual fee, funding, fill and
   exchange assumptions. Taken candidates use measured outcomes and their append-only actual-cost revision id. Untaken,
   vetoed and capacity-skipped candidates receive a deterministic causal simulation under the same frozen policy and
   cost model. Measured and simulated outcomes are stored and reported separately; missing future data is
   `INSUFFICIENT`, not imputed. An `INSUFFICIENT` B1 row keeps its B0 order and counts as retained, so queue, timeout
   and spend state cannot improve B1 by silently dropping opportunities. Forward PAPER confirms transfer; two or three
   days cannot promote it.
4. **Evidence.** Report trades/week, retained-trade rate, net expectancy, profit factor, drawdown, tail loss, costs/R,
   long/short and regime/coin concentration, Brier score, calibration error, coverage and latency.

## Probability target and calibration gate

The probability target is one only when the candidate's net return in R is greater than zero after actual-model fees,
funding and fills at the earlier of its deterministic strategy exit or its strategy-declared maximum-bar horizon. If a
position would still be open at that horizon, use the first causally executable mark at/after it and charge the frozen
exit-cost model. The label definition and cost-model hash are part of the calibration epoch.

A probability remains `UNVERIFIED` unless a calibration window disjoint from fitting, walk-forward evaluation and the
sealed holdout has all of the following:

- at least 500 resolved labels overall and at least 50 labels per reported bin (adjacent bins are merged otherwise);
- pooled expected calibration error at most 0.05 under the preregistered fixed binning rule; and
- Brier score strictly better than the frozen base-rate predictor, with the paired-bootstrap 95% confidence interval
  for Brier improvement wholly above zero. The paired bootstrap is time-blocked and cross-coin clustered, never iid
  over correlated candidate rows.

Every model, prompt-template, sampling-parameter, feature/schema or label/cost-model change opens a new unverified epoch.
Rolling ECE above 0.08 or failure to beat the frozen base-rate predictor for two consecutive resolved windows
automatically demotes the epoch to `UNVERIFIED`.

## Preregistration and holdout

AI-SCAN-01 receives a new dedicated holdout; it may not reuse or read any existing EDGE/newcore sealed holdout. Its dated
window is fixed in the sealed manifest and may not overlap any existing sealed-holdout window, including the newcore
holdout beginning 19 October 2026. Codex seals the dataset manifest and hashed preregistration before evaluation. The
holdout is opened once, after fitting, calibration and anchored walk-forward choices are frozen.

The preregistration fixes B1's co-primary endpoints as improvement in actual-cost net expectancy per resolved opportunity
over B0 and total actual-cost net R/week not below B0. It also requires at least 1,000 eligible candidates and 300
resolved trade opportunities, at least 80% of B0's trades/week, and no more than a 10% relative worsening in maximum
drawdown or CVaR95. Candidate ranking variants and sort keys use Holm correction. Future B2/B3 comparisons require
separate preregistrations and cannot be selected from this holdout.

The remote-analysis queue is bounded. Queue-full, timeout or exhausted spend budget records `INSUFFICIENT` and cannot
delay or fail the engine. The exact queue limit, worker timeout and spend budget are configuration values frozen in the
preregistration and reported with every evaluation.

## Required tests

- point-in-time cutoff/no-look-ahead and restart idempotency;
- point-in-time universe, exchange-filter and funding snapshots reject later-ingested/restated data;
- missing, stale or malformed inputs fail to `INSUFFICIENT`;
- model output cannot mutate the deterministic plan or acquire order authority;
- calibrated and uncalibrated UI labels cannot be confused;
- outcome joins handle partial exits, adds and append-only actual-cost revision ids without double counting;
- untraded simulated outcomes cannot be mixed with measured outcomes;
- remote hangs, timeouts, queue saturation and exhausted spend budgets leave the 15-minute cycle and position
  management unaffected; the queue stays bounded;
- fitting and calibration paths cannot read holdout rows;
- API compatibility, Windows UI screenshots and no console errors; and
- independent Cowork adversarial review plus exact-head CI before any PAPER install.

## Deliberately deferred

- Any cracked/reverse-engineered TradingView client or licensing bypass.
- TradingView webhook ingestion (Phase 11 TV-01).
- Live/news authority, direct execution, veto/risk authority, leverage increases or risk above 1x.
- Parameter mining against the untouched holdout.

Tracking: GitHub issue #199.
