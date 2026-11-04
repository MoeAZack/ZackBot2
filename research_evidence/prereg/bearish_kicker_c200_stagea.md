# Candidate 200 / Bearish Kicker preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C199 tree, refs, history, preregistrations, results and research tools were searched for Bearish Kicker; no implementation was found. Bearish engulfing, belt-hold and other two-candle reversal families are disclosed. This is a distinct directional hypothesis: the bearish candle must open at or below the prior bullish candle's open, gapping through the entire prior real body. It is not a parameter variation or rescue of C199, whose zero-trade rejection remains recorded unchanged. One conventional cell only. All recorded sparse, near-miss and rejected results remain unchanged.

On completed 1h bars, require a bullish candle followed by a bearish candle; each real body must be at least 60% of its own high-low range, and the bearish candle must open at or below the prior bullish open. Enter short next open when the bearish candle closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 200; future family-wise alpha <=0.05/200. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: distinct default-OFF two-candle gap reversal family.
DEFERRED: body/gap variants, folds, holdout, Stage B and activation.
