# Candidate 164 / STARC Bands breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C163 tree, refs, history, preregistrations, results and research tools were searched for STARC Bands, Stoller Average Range Channel and STARC; no implementation was found. Correlation with Keltner, Chande Kroll, Standard Error, Raff, Bollinger and fixed-percentage envelopes is disclosed. STARC uniquely centers fixed ATR multiples on a short simple moving average. One conventional cell only.

On completed 1h bars, compute conventional STARC Bands with SMA(6), Wilder ATR(15), and upper/lower bands at SMA plus/minus `2*ATR`. Enter next open when close crosses above the upper band while close is above EMA200. Exit when close is at/below SMA6, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 164; future family-wise alpha <=0.05/164. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF Stoller SMA/ATR channel family.  
DEFERRED: short cell, alternate windows/multipliers/filters, folds, holdout, Stage B and activation.
