# Candidate 166 / Sine-Weighted Moving Average crossover preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C165 tree, refs, history, preregistrations, results and research tools were searched for Sine-Weighted Moving Average, sine weighted, SINWMA and SWMA; no implementation was found. Correlation with the previously tested SMA/WMA/TMA and other moving-average crossover families is disclosed. SINWMA is distinct because its fixed symmetric sine kernel gives the center of the 20-bar window the greatest weight and both endpoints the least weight. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, compute SINWMA(20) using fixed chronological weights `sin(pi*i/21)` for `i=1..20`, normalized by their sum. Enter next open when close crosses above SINWMA20 while close is above EMA200. Exit when close is at/below SINWMA20, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 166; future family-wise alpha <=0.05/166. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF fixed-kernel moving-average family.  
DEFERRED: short cell, alternate windows/kernel variants/filters, folds, holdout, Stage B and activation.
