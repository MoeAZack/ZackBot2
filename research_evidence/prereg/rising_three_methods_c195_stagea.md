# Candidate 195 / Rising Three Methods preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C194 tree, refs, history, preregistrations, results and research tools were searched for Rising Three Methods; no implementation was found. C180 Three White Soldiers, C193 bullish Marubozu and other multi-bar momentum/candlestick families are disclosed. This cell is distinct because it requires a five-candle sequence with three bearish candles fully contained inside the first bullish candle's range, followed by a bullish breakout. One conventional cell only. All recorded near-misses and rejections remain unchanged and are not rescued or tuned.

On completed 1h bars, require: candle 1 bullish with its real body at least 60% of its range; candles 2-4 each bearish and each full high-low range contained within candle 1's range; candle 5 bullish with its body at least 60% of its range and close above candle 1's close. Enter long next open when candle 5 closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 195; future family-wise alpha <=0.05/195. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF five-candle continuation family.
DEFERRED: bearish counterpart, body/range variants, folds, holdout, Stage B and activation.
