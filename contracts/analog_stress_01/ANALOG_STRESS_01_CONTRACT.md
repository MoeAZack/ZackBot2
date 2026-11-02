# ANALOG-STRESS-01 — session-aware path stress contract

**Status: DRAFT. Base: `851b0cd8ff990f6ed5f66ddde84e650a7c22201f`. Research diagnostic only; no selection, PAPER, or mainnet authority.**

## Purpose

Estimate how a frozen set of historical trades behaves when temporal dependence, Cairo trading sessions, ambiguous intrabar paths, gap opens, and realistic costs are stressed together. The output is a robustness diagnostic—not a synthetic backtest and not evidence that a strategy is profitable.

## Frozen inputs

The run manifest binds the source trade rows, point-in-time market data, 1-minute path data, cost inputs, session definition, block-length rule, random seed, code commit, and tool hashes. Source rows after the declared cutoff are refused. Missing or inconsistent bindings fail closed.

## Sessions and time

UTC timestamps are converted with the IANA zone `Africa/Cairo`; offsets are never hardcoded. The machine contract freezes the session boundaries and the rule for assigning a trade that spans boundaries. Tests must cover both standard-time and DST transitions.

## Joint stress procedure

1. Keep the observed causal path as the primary result.
2. Label each trade's admissible `observed`, `stop_first`, `target_first`, and `gap_open` outcomes using the existing intrabar rules.
3. Resample contiguous Cairo-day blocks so serial dependence, clustering, side, symbol, session, and path ambiguity travel together.
4. Select a permitted path within each sampled trade and recompute fees, slippage, funding, and stop-at-open effects for that path.
5. Report observed, pessimistic, and optimistic bounds separately. Never average them into a single claim.

IID trade bootstrap may appear only as a disclosed sensitivity comparison; it is not the primary estimator.

## Minimum evidence and refusal rules

- Deterministic output for a frozen seed and manifest.
- At least the contract-frozen number of distinct Cairo days and observations per reported session.
- Missing 1-minute data, insufficient cells, impossible path ordering, or cost failure is reported separately and blocks a complete verdict.
- An autocorrelated synthetic fixture must demonstrate that block and IID estimates differ.
- Long/short, same-minute target/stop, and gap-through-stop fixtures are required.
- Every session reports N, day count, path coverage, ambiguity, missing-path rate, and cost completeness.

## Immutable result packet

The packet records input and code hashes, seed, block construction, sampled-block digest, session/DST rules, observed and bound distributions, cost-model identity, exclusions, and verdict. Re-running an existing packet key with different content is refused.

## Authority boundary

ANALOG-STRESS-01 cannot tune parameters, rank strategies, open a holdout, promote a candidate, place an order, change PAPER, or authorize mainnet. A failed diagnostic parks only the tested claim and feeds the candidate ledger.

## Deferred

- Stress estimator implementation.
- Independent synthetic and historical execution.
- Any candidate-specific frozen pack or promotion decision.
