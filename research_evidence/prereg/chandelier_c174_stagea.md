# Candidate 174 / Chandelier Exit breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C173 tree, refs, history, preregistrations, results and research tools were searched for Chandelier Exit and Chandelier stop; no implementation was found. Correlation with ATR channels, volatility stops, Supertrend and price-channel breakouts is disclosed. This cell is distinct because it derives asymmetric long/short stop levels from rolling 22-bar extrema displaced by a Wilder ATR multiple and trades the short-stop upside transition. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, compute Wilder ATR(22), long chandelier `highest(high,22)-3*ATR22`, and short chandelier `lowest(low,22)+3*ATR22`. Enter next open when close crosses above the short chandelier while close is above EMA200. Exit when close is below the long chandelier, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 174; future family-wise alpha <=0.05/174. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF rolling-extreme ATR-stop family.  
DEFERRED: short cell, alternate windows/multipliers/filters, folds, holdout, Stage B and activation.
