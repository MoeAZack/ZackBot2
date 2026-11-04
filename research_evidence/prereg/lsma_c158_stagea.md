# Candidate 158 / Least Squares Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C157 tree, refs, history, preregistrations, results and research tools were searched for Least Squares Moving Average, Linear Regression Moving Average and LSMA; no implementation was found. Correlation with regression trend quality, CFO, CTI, projection oscillator and conventional moving-average cells is disclosed. LSMA uniquely uses the fitted endpoint of an ordinary least-squares line through each fixed trailing window. One conventional cell only.

On completed 1h closes, compute conventional LSMA(20) as the value at index 19 of the OLS line fitted to the trailing 20 closes at indices 0 through 19. Enter next open when close crosses above LSMA while close is above EMA200. Exit when close is at/below LSMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 158; future family-wise alpha <=0.05/158. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF rolling least-squares endpoint family.  
DEFERRED: short cell, alternate lengths/offsets/filters, folds, holdout, Stage B and activation.
