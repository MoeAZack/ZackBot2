# Candidate 151 / Double Exponential Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C150 tree, refs, history, preregistrations, results and research tools were searched for Double Exponential Moving Average and DEMA; no implementation was found. Correlation with EMA, HMA, KAMA, FRAMA, McGinley, VIDYA, ALMA and other moving-average cells is disclosed. DEMA uniquely cancels part of a single EMA's lag by subtracting its twice-smoothed component. One conventional cell only.

On completed 1h closes, compute conventional DEMA(20) as `2*EMA20(close)-EMA20(EMA20(close))`. Enter next open when close crosses above DEMA while close is above EMA200. Exit when close is at/below DEMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 151; future family-wise alpha <=0.05/151. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or REC-02; holdout sealed.

ROADMAP FIT: distinct default-OFF lag-reduced double-exponential-average family.  
DEFERRED: short cell, alternate lengths/filters, folds, holdout, Stage B and activation.
