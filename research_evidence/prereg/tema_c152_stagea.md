# Candidate 152 / Triple Exponential Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C151 tree, refs, history, preregistrations, results and research tools were searched for Triple Exponential Moving Average and TEMA; no implementation was found. Correlation with EMA, DEMA, HMA, KAMA, FRAMA, McGinley, VIDYA, ALMA and other moving-average cells is disclosed. TEMA uniquely combines first-, second- and third-order EMA smooths to cancel additional lag. It is not the previously tested TRIX oscillator. One conventional cell only.

On completed 1h closes, compute conventional TEMA(20) as `3*EMA1-3*EMA2+EMA3`, where each successive EMA uses span 20. Enter next open when close crosses above TEMA while close is above EMA200. Exit when close is at/below TEMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 152; future family-wise alpha <=0.05/152. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or REC-02; holdout sealed.

ROADMAP FIT: distinct default-OFF third-order lag-cancelled exponential-average family.  
DEFERRED: short cell, alternate lengths/filters, folds, holdout, Stage B and activation.
