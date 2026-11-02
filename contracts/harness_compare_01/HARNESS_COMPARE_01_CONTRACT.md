# HARNESS-COMPARE-01 — cross-engine causal parity contract

**Status: DRAFT. Base: `851b0cd8ff990f6ed5f66ddde84e650a7c22201f`. No data run, package install, PAPER authority, or mainnet authority.**

## Purpose

Test whether one frozen ZackBot strategy produces the same causal signals, fills, exits, and costs when expressed through ZackBot's existing golden harness and a later isolated Freqtrade comparison adapter. This is a harness-integrity test, not evidence that the strategy is profitable.

## Frozen comparison pack

The eventual run must bind, by SHA-256, one strategy implementation, its parameters, a point-in-time OHLCV dataset, symbol/timeframe, warm-up length, execution timing, intrabar path rules, fee/slippage/funding inputs, and every tool version. Holdout data is forbidden.

Both engines export a neutral ordered event stream containing candle time, decision time, side, signal, requested and filled quantity, fill price, exit reason, gross P&L, each cost component, and net P&L. Engine-specific fields stay outside the canonical comparison.

## Reuse

- `tests/golden/goldenlib/adapters/` and `tests/golden/goldenlib/compare.py` for deterministic traces and declared divergences.
- `tools/research/manifest.py` and `tools/research/pit.py` for immutable, point-in-time inputs.
- `tools/research/intrabar.py`, `tools/research/costs.py`, and `tools/research/exec_obs.py` for causal path and realistic costs.

Existing adapters and runtime modules are not edited by this slice.

## Required diagnostics

1. Future-shift mutations must change the diagnostic verdict and be detected.
2. Prefix runs at multiple cutoffs must leave all earlier exported events byte-identical.
3. Recursive warm-up runs at several startup-candle counts must converge before the scored interval.
4. Long, short, gap-open, and same-bar stop/target fixtures must be compared.
5. A changed dataset, strategy file, version pin, or cost manifest must fail closed.
6. Base and 1.5x-cost runs must use identical trades; only declared cost outputs may differ.

## Acceptance

- Zero unexplained signal, fill, exit, or cost divergence.
- Every expected semantic difference is explicit in the divergence ledger and is never normalized away.
- Look-ahead mutation tests pass and recursive diagnostics are clean.
- The result packet binds all input, code, adapter, environment, and output hashes.
- No third-party strategy or execution code is trusted or copied into ZackBot.

## Authority boundary

The contract and its local synthetic tests may land without Freqtrade installed. A Freqtrade execution is a later pinned, isolated Evidence step. Any failure blocks only this comparison result. Nothing here may place orders, alter PAPER, open holdout data, select a strategy, or authorize mainnet.

## Deferred

- Adapter implementation and pinned isolated environment.
- Frozen strategy/data pack selection.
- Independent execution and signed comparison result.
- Any strategy promotion decision.
