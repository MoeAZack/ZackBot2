# Candidate 181 / Bullish Hammer preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C180 tree, refs, history, preregistrations, results and research tools were searched for bullish Hammer, Hammer candlestick and lower-shadow Hammer geometry; no implementation was found. Wick-imbalance, liquidity-sweep and other candlestick/rejection families are disclosed. This cell is distinct because it uses the classical within-bar real-body/shadow proportions without requiring a prior-level sweep, outside range or multi-bar formation. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On a completed 1h bar, require a bullish real body (`close>open`), lower shadow `open-low` at least twice the body, and upper shadow `high-close` no larger than the body. Enter next open when the pattern closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 181; future family-wise alpha <=0.05/181. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF single-bar lower-shadow rejection family.  
DEFERRED: bearish counterpart, downtrend/location/body-size/follow-through filters, folds, holdout, Stage B and activation.
