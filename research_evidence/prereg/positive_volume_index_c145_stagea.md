# Candidate 145 / Positive Volume Index preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C144 tree, refs, history, preregistrations, results and research tools were searched for Positive Volume Index and high-volume-only cumulative return; no implementation was found. Direct correlation with C103 Negative Volume Index and general volume-trend cells is disclosed. PVI is distinct because it compounds returns only when completed quote volume rises, while NVI updates only when volume falls. One conventional cell only.

On completed 1h bars, start PVI at 1000 and compound the close-to-close return only when quote volume exceeds the preceding bar; otherwise hold the prior index. Compute conventional EMA255 of PVI. Enter next open when PVI crosses above EMA255 and close is above EMA20. Exit at PVI<EMA255, 24 bars, 2ATR stop or 2R target. The stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 145; future family-wise alpha <=0.05/145. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER or runtime; holdout sealed.

ROADMAP FIT: distinct default-OFF high-volume-selective cumulative-return family.  
DEFERRED: short cell, alternate signal spans/filters, folds, holdout, Stage B and activation.
