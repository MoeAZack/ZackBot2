# Candidate 162 / Standard Error Bands breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C161 tree, refs, history, preregistrations, results and research tools were searched for Standard Error Bands and Standard Error Channel; no implementation was found. Correlation with LSMA, regression trend quality, Bollinger, Keltner, Donchian and fixed-percentage MA envelopes is disclosed. This cell uniquely centers a channel on the rolling least-squares endpoint and scales it by regression residual standard error. One conventional cell only.

On completed 1h closes, fit OLS to indices 0 through 19 across each trailing 20-bar window. The midpoint is the fitted value at index 19; residual standard error is `sqrt(sum(residual^2)/(20-2))`; bands are midpoint plus/minus two standard errors. Enter next open when close crosses above the upper band while close is above EMA200. Exit when close is at/below the midpoint, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 162; future family-wise alpha <=0.05/162. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF regression-residual channel family.  
DEFERRED: short cell, alternate lengths/multipliers/filters, folds, holdout, Stage B and activation.
