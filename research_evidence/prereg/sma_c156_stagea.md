# Candidate 156 / Simple Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C155 tree, refs, history, preregistrations, results and research tools were searched for a direct Simple Moving Average or SMA crossover; no standalone candidate was found. Correlation with the rolling means embedded in other indicators and with EMA, DEMA, TEMA, ZLEMA, WMA, SMMA, HMA, KAMA, FRAMA, McGinley, VIDYA and ALMA is disclosed. This cell uniquely evaluates direct price interaction with an equal-weight finite mean. One conventional cell only.

On completed 1h closes, compute conventional SMA(20) as the equal-weight mean of the trailing 20 closes. Enter next open when close crosses above SMA while close is above EMA200. Exit when close is at/below SMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 156; future family-wise alpha <=0.05/156. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF direct equal-weight moving-average family.  
DEFERRED: short cell, alternate lengths/filters, folds, holdout, Stage B and activation.
