# Candidate 163 / Raff Regression Channel breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C162 tree, refs, history, preregistrations, results and research tools were searched for Raff Regression Channel and Raff Channel; no implementation was found. Correlation with LSMA, Standard Error Bands, Donchian and other breakout channels is disclosed. Raff uniquely sets a symmetric OLS channel width to the greatest vertical high-above-line or line-above-low deviation in the lookback. One conventional cell only.

For each completed 20-bar 1h window, fit OLS to closes at indices 0 through 19. Center the channel on the fitted value at index 19; width is the maximum of every bar's `high-fitted_line` and `fitted_line-low`, and bands are center plus/minus width. At decision bar t, use only the channel from bars t-20 through t-1. Enter next open when the decision close crosses above that prior upper endpoint while above EMA200. Exit when close is at/below the prior midpoint, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 163; future family-wise alpha <=0.05/163. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF maximum-deviation regression-channel family.  
DEFERRED: short cell, alternate lengths/projection conventions/filters, folds, holdout, Stage B and activation.
