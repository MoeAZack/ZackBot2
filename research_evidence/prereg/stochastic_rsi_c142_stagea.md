# Candidate 142 / Stochastic RSI preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C141 tree, refs, history, preregistrations, results and research tools were searched for Stochastic RSI, StochRSI and rolling RSI-range normalization; no implementation was found. Correlation with RSI, stochastic, Connors RSI and other oversold-recovery cells is disclosed. StochRSI uniquely locates RSI14 within its own trailing 14-bar range. One conventional cell only.

On completed 1h closes, compute RSI14 and conventional StochRSI(14) as `100*(RSI-low14)/(high14-low14)`. Enter next open when StochRSI crosses above 20 and close is above EMA200. Exit at StochRSI>=80, 24 bars, 2ATR stop or 2R target. The stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding. Zero/invalid range cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 142; future family-wise alpha <=0.05/142. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER or runtime; holdout sealed.

ROADMAP FIT: distinct default-OFF normalized-RSI recovery family.  
DEFERRED: short cell, K/D smoothing, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
