# VPI-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any VPI-1H result  
Key: `volume_pressure_inventory_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independence

Informed inventory can appear as directional signed-volume pressure while price remains absorbed. VPI-1H asks whether
that pressure predicts the next day. It is sampled once at 00:00 UTC, rather than continuously, to limit turnover. This
is not return-rank strength, BTC residual reversion, funding dislocation, VWAP, breakout/retest, trend pullback, clock-
handoff reversal, ATR shock, or a runtime rule. No cross-sectional or parameter rank is selected.

## Frozen causal rules

- At each completed 00:00 UTC candle, compute 48-hour signed base volume: candle volume times the sign of its completed
  close-to-close return, divided by total volume. Long at pressure >=+10%; short mirrors at <=-10%.
- Require the 48-hour price displacement to remain within one current ATR (absorption), mean 48-hour volume to be at
  least 80% of the trailing seven-day median hourly volume, and the 2 ATR stop to cover at least 5x the 5 bp taker +
  2 bp adverse-slippage round trip. Entry is the next 1h open.
- Exit when pressure crosses zero or via frozen protection: 2 ATR stop, 2R target, 24-hour cap. No partials, pyramiding,
  re-entry on the same decision bar, side pooling, symbol/hour selection, or grid.
- Actual checksum-verified Binance funding is booked using the last verified 1h mark close at each settlement.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either reported BTC
4h EMA200 regime. A survivor runs unchanged on `2025-04-01..2025-09-30`, `2025-10-01..2026-03-31`, and
`2026-04-01..2026-09-28` (exclusive). Final per-side gate: aggregate mean net R >0, PF >1.05, >=200 closes across >=8
symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown <=15R, neither regime mean R below -0.05, and no
symbol >35% of absolute contribution. Sealed holdout remains closed. Multiplicity: two new cells; prior candidates stay
separate in the ledger.

ROADMAP FIT: low-turnover, high-sample point-in-time volume-information hypothesis after fee-heavy candidate 14.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_volume_pressure_inventory_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\vpi1h_stagea\vpi-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
