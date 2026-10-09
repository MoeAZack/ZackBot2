# SCORE-01: point-in-time candidate board and forward evidence

**Status:** contract foundation. It does not add a strategy, change sizing or enable automation.

This contract incorporates the useful engineering lessons from Coil's scanner and published live record while keeping
ZackBot's independent long/short, crypto-first and evidence-first requirements. ZackBot does not copy Coil's fixed
weights, long-only book, leveraged-ETF acceleration or claimed backtest edge.

Machine-readable candidate shape: `contracts/candidate_score_v1.schema.json`.

## 1. Separate facts, never one magic score

Every asset / timeframe / strategy / side candidate publishes separate 0–100 dimensions:

- opportunity;
- entry quality;
- hold quality;
- regime fit;
- evidence readiness;
- operational readiness.

The UI may rank by a versioned composite, but it always shows the components and weights. A high opportunity score can
never hide weak evidence, poor operational readiness or a mismatched regime. Scores never authorize an order or raise
the owner's mechanical risk ceiling.

## 2. Explicit entry state

Every candidate has one state:

- `READY`: eligible for the risk gateway now;
- `SETUP`: near eligibility but not yet actionable;
- `WAIT`: valid idea without an entry;
- `EXTENDED`: move already stretched; do not chase;
- `BREAKING`: structure is failing for the proposed side;
- `STAND_DOWN`: the strategy/regime cell should hold cash or remain inactive.

Direction is separate, so the same states apply independently to LONG and SHORT. ZackBot does not turn a long rule
upside down and call it a calibrated short strategy.

## 3. Cycle priority and stale-input behavior

Every cycle processes protection, exits and reconciliation before new-entry ranking. A stale or missing candidate feed
means no new entries; existing positions continue to be protected and managed from durable local rules and exchange
truth. `STAND_DOWN` is a valid outcome, not a system failure.

Every candidate carries `as_of_ms`, `expires_at_ms`, strategy/version, universe identity and a frozen source-manifest
hash. An expired or mismatched record cannot be promoted into a signal.

## 4. Structural entries and exits

The candidate records a structural stop in an explicit unit when one is known. The later strategy plan may combine:

- quick partial profit or ladder exits;
- a protected runner;
- a rule-based exit when the original thesis/structure stops working;
- a hard protective stop as the backstop;
- the separately proven, strictly bounded micro-DCA or basket policy.

Stops do not guarantee the fill price through gaps. Backtests and UI disclosures preserve gap/slippage risk.

## 5. Honest research and forward record

Research must use point-in-time universes and retain delisted/failed assets where relevant. Fills occur no earlier than
the next executable event and include fees, spread, slippage, funding/borrow, gaps and capacity. Each promoted strategy
is compared with simple benchmarks for the same window and exposure.

Backtest, paper, testnet and live-forward results are separate datasets and labels. A strong backtest cannot overwrite
weak forward evidence. The forward ledger includes every eligible outcome, explicit flat/stand-down periods, exclusions
and corrected-defect policy. Exclusions are counted and reason-coded, never silently removed.

Before a sealed evaluation or public/lead claim, ZackBot commits the canonical candidate/decision digest before the
outcome is known. This proof-of-prior shows the call was not edited later; it does not prove brokerage returns.

## 6. Automation and ML boundary

Mechanical rules and frozen validation gates remain the authority. ML starts in shadow after the Phase 3 data exists.
It may recommend, veto or scale requested risk only within 0–1×; it may not edit its own acceptance tests, entries,
exits, stops or account caps. Champion/challenger changes require a new strategy/model version and forward comparison.
