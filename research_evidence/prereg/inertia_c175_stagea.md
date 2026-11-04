# Candidate 175 / Dorsey Inertia indicator preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C174 tree, refs, history, preregistrations, results and research tools were searched for Inertia indicator, Dorsey Inertia and Inertia oscillator; no implementation was found. C113 tests raw Relative Volatility Index and is explicitly disclosed. This conventional cell is distinct because it applies a fixed 20-bar least-squares endpoint estimator to RVI(10,14), measuring the fitted directional-volatility tendency rather than the raw RVI threshold. One conventional cell only; it is not a rescue or retuning of C113. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h closes, compute the existing conventional RVI(10,14): rolling 10-bar close dispersion assigned by the sign of the close change and Wilder-smoothed over 14 bars. Compute Inertia as the 20-bar least-squares regression endpoint of RVI. Enter next open when Inertia crosses above 50 while close is above EMA200. Exit when Inertia is at/below 50, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 175; future family-wise alpha <=0.05/175. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF fitted directional-volatility tendency family.  
DEFERRED: short cell, alternate RVI/regression windows/thresholds, folds, holdout, Stage B and activation.
