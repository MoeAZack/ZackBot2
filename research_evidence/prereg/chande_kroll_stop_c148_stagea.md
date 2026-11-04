# Candidate 148 / Chande Kroll Stop preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C147 tree, refs, history, preregistrations, results and research tools were searched for Chande Kroll Stop, Kroll Stop and two-stage ATR stop channels; no implementation was found. Correlation with Donchian, ATR breakout and trailing-stop cells is disclosed. CKS uniquely applies an ATR offset to rolling extrema and then a second extrema pass to form asymmetric long and short stop lines. One conventional cell only.

On completed 1h bars, compute Wilder ATR10. The preliminary long stop is highest high10 minus 1 ATR; the preliminary short stop is lowest low10 plus 1 ATR. Conventional CKS(10,1,9) takes the highest preliminary long stop and lowest preliminary short stop over nine bars. Enter next open when close crosses above the upper/short stop while close is above EMA20. Exit when close falls below the lower/long stop, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 148; future family-wise alpha <=0.05/148. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or REC-02; holdout sealed.

ROADMAP FIT: distinct default-OFF two-stage ATR stop-channel breakout family.  
DEFERRED: short cell, alternate windows/multipliers/filters, folds, holdout, Stage B and activation.
