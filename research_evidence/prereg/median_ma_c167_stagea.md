# Candidate 167 / Median Moving Average crossover preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C166 tree, refs, history, preregistrations, results and research tools were searched for Median Moving Average, rolling-median crossover and median crossover; no direct implementation was found. Overlap with the earlier median/MAD dislocation reversion feature and correlation with other moving-average crossover families are disclosed. This cell is distinct because it uses the 20-bar order-statistic median itself as a robust trend center, without dispersion normalization or mean weighting. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, compute the rolling median of the last 20 closes. Enter next open when close crosses above MedianMA20 while close is above EMA200. Exit when close is at/below MedianMA20, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 167; future family-wise alpha <=0.05/167. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF robust order-statistic trend family.  
DEFERRED: short cell, alternate windows/quantiles/filters, folds, holdout, Stage B and activation.
