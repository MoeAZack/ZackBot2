# Candidate 218 / Bearish Counterattack preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C217 tree, refs, history, preregistrations, results and research tools were searched for Bearish Counterattack; no implementation was found. C185 Dark Cloud Cover, C204 Matching High, C202 Separating Lines and C217 Bullish Counterattack are disclosed. This is C217's conventional bearish counterpart: after a bullish candle, a bearish candle opens above the prior close and falls back to approximately that close. It is a new family, not a rescue or retune of C217, which remains rejected. One conventional cell only. All recorded results remain unchanged.

On completed 1h bars, require candle 1 bullish; candle 2 bearish, opening strictly above candle 1's close, and closing within 0.10 ATR14 of candle 1's close. Enter short next open when candle 2 closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 218; future family-wise alpha <=0.05/218. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: distinct default-OFF two-candle bearish counterattack family.
DEFERRED: tolerance/gap variants, folds, holdout, Stage B and activation.
