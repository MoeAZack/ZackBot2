# FDMR-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any FDMR-1H market-data result  
Key: `funding_dislocation_mr_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and data boundary

An unusually negative settled funding rate can mark crowded shorts, and an unusually positive rate crowded longs. FDMR-1H
fades only the first completed hourly recovery candle after a newly settled extreme event. It reads actual Binance USD-M
funding records from the checksum-verified `binance-um-archive-v1` manifest. `calc_time` is the availability time; an event
may affect a decision only when it was already available at that candle's open. No REST tail, current value, forward fill
posing as a fresh event, inferred funding, open interest, premium index or post-result symbol selection is allowed.

This is not CARRY-01 (delta-neutral spot/perpetual cash-and-carry), EDGE-02 BTC residual reversion, EDGE-03 cross-sectional
rank strength, DSR-1H cross-asset dispersion, or any runtime rule. It is a directional, event-time funding-dislocation fade.

## Frozen causal rules

- At each funding event, compute z = (rate - mean) / sample standard deviation from the preceding 90 settled events only;
  require at least 60 prior events and non-zero standard deviation.
- `fresh` is true only on the first complete 1h candle whose open follows the event (`0 < open-event <= 61 minutes`).
- Long: fresh z <= -1.50, preceding six-hour price move negative, current candle bullish and closes above the preceding
  close. Short mirrors every sign. Candle volume must be >= 0.80 times the preceding 24-hour median.
- The 1.5 ATR stop distance must be >= five times the full 5 bp taker + 2 bp adverse-slippage round-trip cost.
- Signal is decided on the closed candle; entry is next open. Exit on funding z normalizing to -0.25 (long) / +0.25
  (short), or frozen management: 1.5 ATR stop, half at +0.8R and breakeven, +1.5R final target, 18-hour cap.
- Actual funding charged for every event with `entry <= event < exit`, signed by side, using the last checksum-verified
  1h mark-price close available at that event. Fees and adverse slippage are charged on every fill.
- One frozen primary specification. No grid, symbol pruning, side pooling, threshold tuning or rescue after results.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side-level reject: mean net R <= 0,
PF <= 1.05, non-positive pre-cost gross expectancy, fewer than 100 closes, or fewer than eight symbols with closes.
Only a development survivor runs unchanged on:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final Stage-A gate per side: aggregate mean net R > 0, PF > 1.05, >= 200 closes over >= eight symbols, at least two of
three positive folds, median >= eight closes/month, maximum drawdown <= 15R, and no symbol >35% of absolute contribution.
The sealed post-2026-09-28 holdout remains untouched. Multiplicity: two new cells. Failure is recorded and parked.

ROADMAP FIT: frequent, orthogonal event-driven screen after candidate 9's sparse positive long cell.  
DEFERRED: Stage B, holdout, PAPER/testnet, runtime settings, mainnet and all post-result rescue.

## Run command

```powershell
py -3.14 tools/research/run_funding_dislocation_candidate.py --cache <1H_CACHE> --regime-cache <4H_CACHE> `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long --out <OUT>\fdmr-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to the frozen folds.
