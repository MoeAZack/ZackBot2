# Candidate 141 / Relative Momentum Index preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C140 tree, refs, history, preregistrations, results and research tools were searched for Relative Momentum Index, RMI and lagged-change Wilder ratios; no implementation was found. Correlation with RSI, C126 TSI, C130 CMO and other momentum cells is disclosed. RMI uniquely applies the RSI ratio to five-bar changes before fixed Wilder-20 smoothing. One conventional cell only.

On completed 1h closes, compute conventional RMI(5,20): five-bar change, separated positive/negative components, each Wilder-smoothed over 20 bars, then `100*up/(up+down)`. Enter next open when RMI crosses above 50 and close is above EMA20. Exit at RMI<=50, 24 bars, 2ATR stop or 2R target. The stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding. Zero/invalid denominator cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 141; future family-wise alpha <=0.05/141. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER or runtime; holdout sealed.

ROADMAP FIT: distinct default-OFF lagged-change Wilder momentum-ratio family.  
DEFERRED: short cell, alternate momentum/smoothing/thresholds/filters, folds, holdout, Stage B and activation.
