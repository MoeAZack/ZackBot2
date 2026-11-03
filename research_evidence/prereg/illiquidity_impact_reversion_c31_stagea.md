# Candidate 31 / illiquidity-impact reversion long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-31 result  
Key: `illiquidity_impact_reversion_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git branch/history were searched for `amihud`, `illiquidity`,
`price[-_ ]impact`, `return.*quote.?volume`, `quote.?volume.*return`, and `impact reversion`. No strategy, preregistration,
run packet or result matched. Current-tree hits were only generic capacity metrics using notional/quote-volume. This family
is therefore not a rerun of candidates 1-30 or hidden review-only packs.

## Hypothesis

Temporary downside price impact should mean-revert when a 1h loss is unusually large per dollar of quote volume rather
than supported by proportionate liquidity. At a completed bar, enter long when return <= -1.5 ATR/close, absolute return
divided by quote volume is >=3 times its prior 168-bar median, quote volume is at least its prior 168-bar median, and a
2 ATR stop is >=5 times modeled round-trip fee+slippage. Fill next open. This differs from the price-only transient-jump
candidate and the taker/volume-pressure candidates by making return-per-dollar impact the primary state variable.

## Frozen execution and costs

- Parameters: 168-bar baselines, 3x impact, 1.5 ATR shock, median quote-volume floor, 5x cost admission.
- Management: 2 ATR stop, 1.5R target, eight-bar cap, earlier exit at EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: orthogonal post-inventory liquidity-impact scout with explicit all-branch duplicate check.  
DEFERRED: short cell, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
