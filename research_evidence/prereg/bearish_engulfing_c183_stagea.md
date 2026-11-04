# Candidate 183 / Bearish Engulfing preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C182 tree, refs, history, preregistrations, results and research tools were searched for Bearish Engulfing and real-body engulfing; no bearish implementation was found. C48 bearish outside-range breakdown and other candlestick/reversal families are disclosed. This cell is distinct because it requires only full engulfment of a bullish real body by a bearish real body; it does not require high-low range engulfment, ATR expansion, a gap, or a prior-level break. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, require a bullish prior candle and bearish current candle, with current open at/above the prior close and current close at/below the prior open. Boundary equality is allowed for continuous crypto opens. Enter short next open when the completed pattern closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 183; future family-wise alpha <=0.05/183. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF two-candle real-body reversal family.  
DEFERRED: trend/location/body-size/follow-through filters, folds, holdout, Stage B and activation.
