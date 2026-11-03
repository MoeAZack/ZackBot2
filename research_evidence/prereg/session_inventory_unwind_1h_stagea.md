# SIU-1H Stage-A preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**  
Frozen: 2 November 2026, Cairo, before any SIU-1H result  
Key: `session_inventory_unwind_1h`; timeframe: 1h; long and short judged separately

## Hypothesis and independence

Inventory accumulated during one liquidity session may unwind when Europe and the US become active. SIU-1H evaluates
only the 08:00 and 16:00 UTC hourly candles, after an eight-hour displacement and an opposite-direction closed-candle
reversal. It is not ORH-15's opening-range break-and-hold, VRC-15's session VWAP continuation, a continuously scanned
range/VWAP rule, ranks, trend acceleration, funding dislocation, breakout/retest, or candidate 13's 4h slow-trend shock.
The fixed UTC clock is intentional and unaffected by Cairo daylight-saving changes.

## Frozen causal rules

- Decision candles open at 08:00 or 16:00 UTC and must close before the signal exists. The displacement is close at the
  preceding candle minus close eight completed hours earlier; the decision candle is excluded.
- Long: displacement <= -1.5 ATR(14) measured at the preceding close, then a bullish decision candle closing above the
  prior close. Short mirrors every sign.
- Decision volume >=80% of the preceding 24h median. The 1.5 ATR stop must be >=5x the 5 bp taker + 2 bp adverse-
  slippage round trip. Entry is the next hourly open.
- Exit on EMA9 mean touch or frozen protection: 1.5 ATR stop, half at +0.8R and breakeven, +1.5R final target, six-hour
  cap. No pyramiding; same-decision-bar re-entry locked.
- Actual checksum-verified Binance funding is booked using the last verified 1h mark close available at each event.
  One fixed primary; no hour, displacement, direction or symbol grid.

## Stage-A chronology and gates

CORE-20 development: `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Immediate side reject: mean net R <=0, PF <=1.05,
non-positive pre-cost gross expectancy, <100 closes, <8 symbols, max drawdown >15R, or <20 closes in either reported
BTC-above/below-EMA200 regime. Only a survivor runs unchanged on:

1. `2025-04-01..2025-09-30`
2. `2025-10-01..2026-03-31`
3. `2026-04-01..2026-09-28` (exclusive)

Final gate per side: aggregate mean net R >0, PF >1.05, >=200 closes across >=8 symbols, >=2/3 positive folds, median
>=8 closes/month, max drawdown <=15R, neither regime mean R below -0.05, and no symbol >35% of absolute contribution.
The sealed holdout remains closed. Multiplicity: two new cells; all prior candidates remain separate ledger entries.

ROADMAP FIT: dense 1h session-handoff hypothesis after sparse 4h candidate 13.  
DEFERRED: holdout, Stage B, PAPER/testnet, runtime, mainnet, tuning and hour/symbol selection.

## Run command

```powershell
python tools/research/run_session_inventory_unwind_candidate.py --cache <1H_CACHE> --regime-cache <4H_CACHE> `
  --funding-manifest research_evidence/manifests/binance-um-archive-v1.json.gz `
  --funding-store C:\Dev\ZackBot2_data\binance_um --window 2024-10-14T00:00:00Z/2025-04-01T00:00:00Z `
  --tier CORE-20 --side long --out <OUT>\siu-long-dev.json
```

Repeat unchanged for short. Only a development survivor proceeds to folds.
