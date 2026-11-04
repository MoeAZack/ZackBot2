# Candidate 182 / Bullish Engulfing preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C181 tree, refs, history, preregistrations, results and research tools were searched for Bullish Engulfing and real-body engulfing; no implementation was found. C47 bullish outside-range expansion and other candlestick/reversal families are disclosed. This cell is distinct because it requires only full engulfment of a bearish real body by a bullish real body; it does not require high-low range engulfment, ATR expansion, a gap, or a prior-level sweep. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, require a bearish prior candle and bullish current candle, with current open at/below the prior close and current close at/above the prior open. Boundary equality is allowed for continuous crypto opens. Enter next open when the completed pattern closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 182; future family-wise alpha <=0.05/182. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF two-candle real-body reversal family.  
DEFERRED: bearish counterpart, trend/location/body-size/follow-through filters, folds, holdout, Stage B and activation.
