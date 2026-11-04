# Candidate 223 / Long-Legged Doji short preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. This is the independently preregistered directional counterpart to C222, preserving exactly the same Long-Legged Doji geometry: body <=10% of range and each shadow >=35% of range. Only the fixed economic direction changes: a signal below EMA200 enters short. C222's strongly negative long result remains rejected and does not alter any C223 parameter, exit, cost, universe, or gate. The complete C1-C222 tree was searched; no prior short implementation was found. One conventional cell only. All recorded results remain unchanged.

On a completed 1h candle with positive high-low range, require absolute real body <=10% of range and both upper and lower shadows >=35% of range. Enter short next open when the signal candle closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 223; future family-wise alpha <=0.05/223. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: distinct default-OFF short directional cell for the two-sided doji family.
DEFERRED: threshold/location variants, folds, holdout, Stage B and activation.
