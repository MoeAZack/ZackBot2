# EDGE-04 orchestration addendum

Status: **frozen before market-data access or any result, 26 October 2026 Cairo**. The machine-readable authority is
`EDGE04_ORCHESTRATION_ADDENDUM_v1.json`.

The four primaries share the existing 10,000 USDT book: 1% risk, four positions, 3x gross leverage, one position per
symbol, exits before entries, one wallet snapshot per cycle, and frozen symbol ordering. B0 is 10,000 deterministic
matched books per primary, without replacement inside a book. B1 is equal-weight side-matched passive using exact
executable opens and the same costs.

The non-selective stress set is frozen in the JSON. The 1.5x row scales fees, slippage and signed funding magnitudes
by 1.5. The 60-second latency row requires the exact causal 1m bar opening at scheduled entry and available at +60s;
its close is the delayed reference. Missing coverage is incomplete and can never be proxied from 4h or 15m data.

No market data, holdout, PAPER state or runtime state was opened or changed by this addendum.
