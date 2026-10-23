# TA-SCOUT-01 — fixed-role shadow committee

## Roadmap fit

Phase 10 (ML in shadow), supporting EDGE-01. This is a research observer over ZackBot's own causal decision ledger and
completed deterministic backtests. It does not add a strategy, alter the current cohort, or place an LLM in the order
path.

## Roles and output

Every accepted analyst response contains exactly one bounded review from each role:

- regime;
- execution/liquidity;
- catalyst;
- risk/portfolio;
- bull case;
- bear case.

Each role returns only a stance, confidence and short evidence statement. Missing point-in-time evidence must produce
`insufficient`. ZackBot derives committee coverage, weighted stance and a numeric disagreement score deterministically.
The analyst may also rank at most four strategy or family names already present in the aggregate. Rankings are research
priorities, never trade instructions.

The aggregate exposes numeric regime performance, portfolio-pressure readings and source coverage. News coverage is
explicitly zero until the Phase 11 point-in-time `NewsProvider` exists; current web search or model knowledge may not fill
that gap.

## Safety invariants

- PAPER only and off by default.
- Report-only API: no settings, strategy or order mutation surface.
- No raw candles, account/order identifiers, credentials or arbitrary logs enter the prompt.
- Malformed, incomplete, duplicated-role or out-of-range responses become `NO_ANALYSIS`.
- Model output is untrusted and cannot change signals, eligibility, sizing, stops, exits, rotation or orders.
- Existing deterministic engine protections remain the only order authority.

## Pre-registered evaluation

When enough causally aligned committee observations exist, replay the same tape and execution assumptions through:

| Variant | Meaning |
| --- | --- |
| B0 | Current ZackBot baseline, unchanged |
| B1 | Baseline plus committee ranking only |
| B2 | B1 plus catalyst/risk veto simulation |
| B3 | Full fixed-role committee simulation |

Compare net expectancy, profit factor, drawdown, tail loss, costs, stability by regime/side/coin, calibration and trade
retention. B1–B3 remain shadow. They do not advance beyond shadow without a separate Codex acceptance decision,
point-in-time inputs, sealed walk-forward evidence and independent review. The frequent-trading objective is represented
by reporting retained trades against B0; a candidate that removes most opportunities is not promoted merely because its
small surviving sample looks profitable.

## Deliberately deferred

- Investor-personality agents.
- Per-signal remote LLM calls in the 15-minute execution path.
- Live news/catalyst authority before Phase 11 causal data exists.
- Broker/exchange execution driven by analyst prose.
