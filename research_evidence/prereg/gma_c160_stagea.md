# Candidate 160 / Geometric Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C159 tree, refs, history, preregistrations, results and research tools were searched for Geometric Moving Average and GMA; no implementation was found. Correlation with SMA, WMA, SMMA, TMA, VWMA, LSMA, EMA, DEMA, TEMA, ZLEMA, HMA, KAMA, FRAMA, McGinley, VIDYA and ALMA is disclosed. GMA uniquely averages log prices and maps back to price space, treating proportional moves symmetrically. One conventional cell only.

On completed positive 1h closes, compute conventional GMA(20) as `exp(mean(log(close), 20 bars))`. Enter next open when close crosses above GMA while close is above EMA200. Exit when close is at/below GMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 160; future family-wise alpha <=0.05/160. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF multiplicative geometric-average family.  
DEFERRED: short cell, alternate lengths/filters, folds, holdout, Stage B and activation.
