# Candidate 177 / Classic floor-pivot breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C176 tree, refs, history, preregistrations, results and research tools were searched for floor pivots, classic pivot points and central pivot range; no implementation was found. Correlation with higher-timeframe support/resistance and breakout families is disclosed. This cell is distinct because its fixed levels are calculated only from the prior UTC day's high, low, and final close, rather than rolling intraday windows. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

For every completed 1h bar, use only the fully completed prior UTC day: `P=(H+L+C)/3`, `R1=2P-L`, `S1=2P-H`. Enter next open when the hourly close crosses above prior-day R1 while close is above EMA200. Exit when close is at/below prior-day P, after 24 bars, at 2ATR stop or 2R target. The first UTC day has no levels. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 177; future family-wise alpha <=0.05/177. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF completed-prior-day support/resistance family.  
DEFERRED: short cell, other pivot systems/levels/session boundaries, folds, holdout, Stage B and activation.
