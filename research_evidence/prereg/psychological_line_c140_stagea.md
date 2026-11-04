# Candidate 140 / Psychological Line preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C139 tree, refs, history, preregistrations, results and research tools were searched for Psychological Line, PSY and rolling up-close frequency; no implementation was found. Momentum and binary-direction correlation is disclosed, but PSY uniquely measures the percentage of positive close-to-close changes in a fixed window without using return magnitudes. One conventional cell only.

On completed 1h bars, compute PSY(12) as 100 times the fraction of the last 12 close-to-close changes that are positive. Enter next open when PSY crosses above 50 and close is above EMA20. Exit at PSY<=50, 24 bars, 2ATR stop or 2R target. The stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 140; future family-wise alpha <=0.05/140. PAPER remains BLOCK. No rescue, folds, holdout, PAPER or runtime; holdout sealed.

ROADMAP FIT: distinct default-OFF fixed-window directional-frequency family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
