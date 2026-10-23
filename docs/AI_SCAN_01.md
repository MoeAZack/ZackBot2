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
- a setup-quality probability only when the active model/version has passed frozen calibration evidence, otherwise
  `UNVERIFIED`;
- the causal entry zone, invalidation/stop and target plan copied from the immutable deterministic snapshot;
- estimated fees, funding, slippage and dollar/R risk from the measured execution model; and
- the later measured outcome, once available, compared with the original deterministic decision.

Probability, evidence coverage and model confidence are distinct fields. The UI must not collapse them into one
marketing-style percentage.

## Safety invariants

- PAPER/testnet and shadow-only. New UI/API surfaces default off until installed and verified.
- No order, settings, sizing, eligibility, stop, exit, rotation or strategy authority.
- Remote model analysis is queued and asynchronous; it cannot block a 15-minute cycle or position management.
- Inputs contain only information available at the decision cutoff. Missing, stale, malformed or insufficient inputs
  produce `INSUFFICIENT`, never an inferred score.
- Current web/model knowledge cannot substitute for missing point-in-time news. Phase 11 `NewsProvider` remains separate.
- No credentials, exchange/order identifiers, arbitrary logs or raw private state enter a prompt.
- Model prose is untrusted and cannot modify the deterministic trade plan.
- Existing shared risk gateway remains the only possible future authority path; AI can never raise risk above 1x.

## Deliverables

1. **Immutable signal ledger.** One versioned, restart-idempotent record per candidate/decision with cutoff, feature and
   schema hashes, strategy snapshot, deterministic decision, proposed plan, committee result, model/version,
   coverage/disagreement and a causally joined later outcome. Ambiguous joins fail closed.
2. **Scanner API and UI.** Sortable cross-coin table and per-signal detail. Labels clearly distinguish `RULES`,
   `AI SHADOW`, `UNVERIFIED` and `INSUFFICIENT`.
3. **Evaluation harness.** Preregister B0 as the unchanged deterministic baseline and B1 as scanner ranking only.
   Evaluate exact runtime rules on anchored walk-forward and an untouched holdout with actual fee, funding, fill and
   exchange assumptions. Forward PAPER confirms transfer; two or three days cannot promote it.
4. **Evidence.** Report trades/week, retained-trade rate, net expectancy, profit factor, drawdown, tail loss, costs/R,
   long/short and regime/coin concentration, Brier score, calibration error, coverage and latency.

## Required tests

- point-in-time cutoff/no-look-ahead and restart idempotency;
- missing, stale or malformed inputs fail to `INSUFFICIENT`;
- model output cannot mutate the deterministic plan or acquire order authority;
- calibrated and uncalibrated UI labels cannot be confused;
- outcome joins handle partial exits, adds and actual-cost revisions without double counting;
- API compatibility, Windows UI screenshots and no console errors; and
- independent Cowork adversarial review plus exact-head CI before any PAPER install.

## Deliberately deferred

- Any cracked/reverse-engineered TradingView client or licensing bypass.
- TradingView webhook ingestion (Phase 11 TV-01).
- Live/news authority, direct execution, veto/risk authority, leverage increases or risk above 1x.
- Parameter mining against the untouched holdout.

Tracking: GitHub issue #199.
