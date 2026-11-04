# Candidate 154 / Weighted Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C153 tree, refs, history, preregistrations, results and research tools were searched for a direct Weighted Moving Average or WMA crossover; no standalone candidate was found. Correlation is disclosed: the same linearly increasing weights appear as components inside Coppock and HMA, while EMA, DEMA, TEMA, ZLEMA, KAMA, FRAMA, McGinley, VIDYA and ALMA are separate tested baselines. This cell uniquely evaluates direct price interaction with the finite WMA itself. One conventional cell only.

On completed 1h closes, compute conventional WMA(20), assigning weights 1 through 20 from oldest to newest and dividing by their sum. Enter next open when close crosses above WMA while close is above EMA200. Exit when close is at/below WMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 154; future family-wise alpha <=0.05/154. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or REC-02; holdout sealed.

ROADMAP FIT: distinct default-OFF direct finite linear-weighted-average family.  
DEFERRED: short cell, alternate windows/filters, folds, holdout, Stage B and activation.
