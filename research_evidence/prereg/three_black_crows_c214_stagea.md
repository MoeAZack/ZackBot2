# Candidate 214 / Three Black Crows preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C213 tree, refs, history, preregistrations, results and research tools were searched for Three Black Crows; no implementation was found. C180 Three White Soldiers and the later candlestick families are disclosed. This is C180's conventional bearish counterpart and is distinct from the C213 Evening Star reversal: it requires three consecutive declining bearish bodies with nested opens. It is not a rescue or retune of C213, which remains a DD-gate rejection. One conventional cell only. All recorded results remain unchanged.

On completed 1h bars, require three consecutive bearish candles with strictly declining closes. Candle 2's open must lie within candle 1's real body, and candle 3's open must lie within candle 2's real body, boundaries inclusive. Enter short next open when candle 3 closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 214; future family-wise alpha <=0.05/214. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: distinct default-OFF three-candle bearish continuation family.
DEFERRED: body/wick variants, folds, holdout, Stage B and activation.
