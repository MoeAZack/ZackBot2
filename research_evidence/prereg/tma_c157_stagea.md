# Candidate 157 / Triangular Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C156 tree, refs, history, preregistrations, results and research tools were searched for Triangular Moving Average and TMA; no implementation was found. Correlation with SMA, WMA, SMMA, EMA, DEMA, TEMA, ZLEMA, HMA, KAMA, FRAMA, McGinley, VIDYA and ALMA is disclosed. TMA uniquely applies a finite symmetric triangular kernel by smoothing a simple average with another simple average. One conventional cell only.

On completed 1h closes, compute conventional even-length TMA(20) as SMA10 followed by SMA11, yielding 20 effective input bars and symmetric triangular weights. Enter next open when close crosses above TMA while close is above EMA200. Exit when close is at/below TMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 157; future family-wise alpha <=0.05/157. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF symmetric triangular-weight moving-average family.  
DEFERRED: short cell, alternate lengths/filters, folds, holdout, Stage B and activation.
