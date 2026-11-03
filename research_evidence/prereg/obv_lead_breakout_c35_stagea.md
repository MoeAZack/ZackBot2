# Candidate 35 / OBV lead breakout long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-35 result  
Key: `obv_lead_breakout_long_1h`; timeframe: 1h; long only

## Duplicate-search evidence

Before freezing, the current tree and every Git ref/history were searched for on-balance volume, `OBV`, cumulative
signed volume, signed-volume cumulative sums, and volume-breakout strategies. No strategy, preregistration, packet or
result matched. The existing volume-pressure inventory and aggressor-flow absorption trials use one-bar taker or candle
proxies; this family uses a causal multi-bar cumulative signed quote-volume state and requires it to lead price.

## Hypothesis

Cumulative signed quote volume can reveal accumulation before a price breakout. At a completed 1h bar, enter long when
OBV (quote volume signed by close-to-close direction) exceeds its prior 48-bar high while close remains below its own
prior 48-bar high, is above EMA50, and closes above the current open. Current quote volume must be at least its prior
48-bar median. A 2 ATR stop must be at least five times modeled round-trip fee+slippage. Fill at the next open.

## Frozen execution and costs

- Parameters: 48-bar OBV/price/volume baselines, 1.0x quote-volume participation, 5x cost admission.
- Management: 2 ATR stop, 2R target, 16-bar cap, earlier exit below EMA20; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- One long cell only; no grid, short inversion or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: distinct frequent volume-state continuation scout after candidate 34.  
DEFERRED: short cell, OBV/window alternatives, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.
