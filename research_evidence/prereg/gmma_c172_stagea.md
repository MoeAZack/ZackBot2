# Candidate 172 / Guppy Multiple Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C171 tree, refs, history, preregistrations, results and research tools were searched for Guppy Multiple Moving Average, GMMA and moving-average ribbon; no implementation was found. Correlation with EMA crossover and other moving-average trend cells is disclosed. This cell is distinct because it requires complete separation of the canonical six-member short trader ribbon from the canonical six-member long investor ribbon, rather than one price/mean or mean/mean crossing. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h closes, compute canonical GMMA short EMAs `(3,5,8,10,12,15)` and long EMAs `(30,35,40,45,50,60)`. Enter next open on the transition to every short EMA being above every long EMA, while close is above EMA200. Exit when the ribbons cease complete bullish separation, after 24 bars, at 2ATR stop or 2R target. Each EMA uses its own period as minimum warmup. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 172; future family-wise alpha <=0.05/172. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF canonical multi-timescale EMA-ribbon family.  
DEFERRED: short cell, alternate ribbon periods/separation filters, folds, holdout, Stage B and activation.
