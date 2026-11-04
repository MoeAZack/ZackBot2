# Candidate 150 / Arnaud Legoux Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C149 tree, refs, history, preregistrations, results and research tools were searched for Arnaud Legoux Moving Average, ALMA and offset Gaussian-weighted averages; no implementation was found. Correlation with HMA, KAMA, FRAMA, McGinley, VIDYA and other moving-average cells is disclosed. ALMA uniquely applies a fixed asymmetric Gaussian kernel controlled by offset and sigma, without recursion or volatility adaptation. One conventional cell only.

On completed 1h closes, compute conventional ALMA(9, offset .85, sigma 6): weights are Gaussian across the trailing nine observations, centered at `.85*(9-1)` with standard-deviation parameter `9/6`, then normalized to sum to one. Enter next open when close crosses above ALMA while close is above EMA200. Exit when close is at/below ALMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 150; future family-wise alpha <=0.05/150. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or REC-02; holdout sealed.

ROADMAP FIT: distinct default-OFF asymmetric Gaussian-weighted-average family.  
DEFERRED: short cell, alternate windows/offsets/sigma/filters, folds, holdout, Stage B and activation.
