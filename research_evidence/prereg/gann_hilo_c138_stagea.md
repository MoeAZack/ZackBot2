# Candidate 138 / Gann HiLo Activator preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. Complete C1-C137 ledger/tree/ref/history search found no Gann HiLo/HiLo Activator implementation. Correlation with moving-average and high/low trend cells is disclosed; this family uniquely switches persistent state when close breaches the prior completed three-bar mean high/low.

On completed 1h bars, switch bullish when close exceeds the prior three-bar mean high, bearish when below prior three-bar mean low, otherwise retain state. Enter next open on the bullish transition with close>EMA20; exit bearish state, 24 bars, 2ATR stop or 2R. Stop >=5x 0.14% modeled round-trip cost; 5bp taker +2bp slippage/fill plus actual funding.

CORE-20 dev window fixed. PASS gross>0, mean R>0, PF>1.05, >=100 trades, >=8 symbols, >=20/regime, DD<=15R. Family 138, future alpha <=.05/138. PAPER BLOCK; no rescue/folds/holdout/runtime.
