# Candidate 161 / Moving Average Envelope breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C160 tree, refs, history, preregistrations, results and research tools were searched for Moving Average Envelope, MA Envelope and fixed-percentage Price Envelope; no implementation was found. Correlation with SMA crossover, Bollinger, Keltner, Donchian and ATR-scaled robust-location bands is disclosed. This cell uniquely uses a fixed proportional band around an equal-weight mean, independent of realized volatility. One conventional cell only.

On completed 1h closes, compute SMA(20) and fixed envelopes at SMA times `1.025` and `0.975`. Enter next open when close crosses above the upper envelope while close is above EMA200. Exit when close is at/below the SMA midpoint, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 161; future family-wise alpha <=0.05/161. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF fixed-percentage trend-envelope family.  
DEFERRED: short cell, alternate lengths/envelope widths/filters, folds, holdout, Stage B and activation.
