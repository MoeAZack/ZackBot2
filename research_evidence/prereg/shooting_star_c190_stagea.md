# Candidate 190 / Shooting Star preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C189 tree, refs, history, preregistrations, results and research tools were searched for Shooting Star and inverted-hammer geometry; no implementation was found. C181 Bullish Hammer and other candlestick/reversal families are disclosed. This cell is distinct because it requires a bearish real body with a dominant upper shadow, the directional counterpart to C181's bullish body with dominant lower shadow. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On a completed 1h bar, require a bearish real body (`open>close`), upper shadow `high-open` at least twice the body, and lower shadow `close-low` no larger than the body. Enter short next open when the pattern closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 190; future family-wise alpha <=0.05/190. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF one-candle upper-rejection family.  
DEFERRED: bullish inverted-hammer and body/shadow/location/follow-through variants, folds, holdout, Stage B and activation.
