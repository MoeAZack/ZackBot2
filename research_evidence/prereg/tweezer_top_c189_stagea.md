# Candidate 189 / Tweezer Top preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C188 tree, refs, history, preregistrations, results and research tools were searched for Tweezer Top; no implementation was found. C45 separated equal-high cluster breakout, C188 Tweezer Bottom and other candlestick/reversal families are disclosed. This cell is distinct because it requires adjacent bullish-then-bearish candles whose completed highs match within a fixed 0.10 ATR; it does not search a trailing cluster or trade a breakout. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, require a bullish prior candle, bearish current candle and absolute difference between their highs no greater than 0.10 current ATR. Enter short next open when the completed pattern closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 189; future family-wise alpha <=0.05/189. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF adjacent equal-extreme reversal family.  
DEFERRED: tolerance/location/follow-through variants, folds, holdout, Stage B and activation.
