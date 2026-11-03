# MPD-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any MPD-1H result  
Key: `mark_price_dislocation_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independent information

Liquidation or aggressive-flow pressure can move a futures contract's last trade away from Binance's independently
calculated mark price. MPD-1H tests whether fixed-time deviations revert. It uses checksum-verified point-in-time
`markPriceKlines`, not a price oscillator, trend, rank, volume-pressure proxy, funding rate, VWAP, breakout, wick or
session-return rule. The signal is unavailable unless the real mark archive is present.

## Frozen causal rules

- At completed 00:00/04:00/08:00/12:00/16:00/20:00 UTC candles, basis is `(contract close - mark close) / mark close`,
  using the independently archived mark candle with the identical close timestamp. Long at basis <=-8 bp; short mirrors
  at >=+8 bp. Entry is the next hourly open.
- Require the 1.5 ATR stop to cover at least 5x the 5 bp taker + 2 bp adverse-slippage round trip. Exit when basis crosses
  zero or via 1.5 ATR stop, 1.5R target or eight-hour cap. No partials, pyramiding, side pooling, symbol/hour selection or
  parameter grid. Actual funding uses verified event rates and the last causal archived mark close.

## Stage-A chronology and frozen gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either reported BTC
4h EMA200 regime. A survivor runs unchanged on the three established chronological folds. Final per-side gate: aggregate
mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median >=8 closes/month, max drawdown
<=15R, neither regime mean R below -0.05, and no symbol >35% absolute contribution. Sealed holdout stays closed.
Multiplicity: two new cells; prior failures and parked observations remain separate.

ROADMAP FIT: independent exchange-microstructure candidate 17 after price-distribution candidate 16.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and subgroup selection.

## Run command

```powershell
python tools/research/run_mark_price_dislocation_candidate.py --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 --side long `
  --out C:\Dev\ZackBot_cowork_data\mpd1h_stagea\mpd-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to chronological folds.
