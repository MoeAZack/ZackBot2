# Candidate 30 / daily-range participation long preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 3 November 2026, Cairo, before any candidate-30 result  
Key: `daily_range_participation_long_1h`; timeframe: 1h; long only

## Hypothesis

After the emitted-strategy inventory was exhausted and all Git branches were checked for hidden prior evidence, this is
a new, simple 1h participation breakout. On a completed bar, enter long when close exceeds the previous 24 completed
bars' high, EMA50 > EMA200, volume is >=1.5 times the prior 48-bar median, and close lies in the top 25% of the bar.
The decision fills at next open. It is distinct from the prior 15m compression/retest and UTC opening-range candidates:
there is no compression, retest, UTC session anchor or opening range.

## Frozen execution and costs

- Parameters: channel 24, volume baseline 48, volume multiple 1.5, close location 0.75.
- Management: 2 ATR stop, 3 ATR trail, 24-bar cap; exit earlier on close below EMA20 or EMA50 <= EMA200; re-entry lock.
- Realistic 5 bp taker fee and 2 bp adverse slippage per fill; actual checksum-verified funding using causal 1h marks.
- No grid, alternate side, threshold variant or management diagnostic. Default-OFF; no PAPER/runtime change.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Reject if gross <=0, mean net R <=0, PF <=1.05,
<100 closes, <8 symbols, max DD >15R, or <20 closes in either BTC regime. Inspect every gate before folds. A survivor
runs unchanged on three chronological folds. Final: mean R >0, PF >1.05, >=200 closes/8 symbols, >=2/3 positive folds,
median >=8 closes/month, DD <=15R, neither regime mean below -0.05R, no symbol >35% absolute contribution. Holdout sealed.

ROADMAP FIT: first new scout after exhausting and deduplicating the defined strategy inventory.  
DEFERRED: alternate side, holdout, Stage B, PAPER/testnet, runtime, mainnet and rescue.

## Run command

```powershell
python tools/research/run_daily_range_participation_c30.py `
  --cache C:\Dev\ZackBot_research_cache\candles_1h_fib02 `
  --regime-cache C:\Dev\ZackBot_cowork_data\candles_4h_core_research `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um `
  --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z --tier CORE-20 `
  --out C:\Dev\ZackBot_cowork_data\drplong1h_c30\drp-long-dev.json
```
