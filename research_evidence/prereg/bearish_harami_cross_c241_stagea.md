# Candidate 241 / Bearish Harami Cross preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C240 tree, refs, history, preregistrations, results and research tools were searched for bearish Harami Cross; no implementation was found. C187 bearish Harami, C240 bullish Harami Cross and prior doji families are disclosed. This is a conventional but distinct subset: C187 accepts a normal inside real body, while C241 requires the inside candle to be a doji. It is the fixed short-side counterpart to C240 and was selected independently, not as a rescue or alteration of C240's rejection or any prior result. One cell only.

On completed 1h bars, require a bullish candle followed by a doji whose absolute real body is no more than 0.10 of its full high-low range and whose entire real body is inside the prior bullish real body. Enter short next open when the completed doji closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 241; future family-wise alpha <=0.05/241. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime, mainnet or BASE-CLEAN work; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: next distinct default-OFF conventional two-candle bearish reversal family.
DEFERRED: variants, folds, holdout, Stage B and activation.
