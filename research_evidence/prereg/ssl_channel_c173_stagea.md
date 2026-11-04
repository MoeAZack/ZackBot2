# Candidate 173 / SSL Channel preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C172 tree, refs, history, preregistrations, results and research tools were searched for SSL Channel and SSL high/low state; no implementation was found. Correlation with Gann HiLo, high/low channels and moving-average trend cells is disclosed. This cell is distinct because it applies a persistent three-zone state machine around separate SMA10(high) and SMA10(low) boundaries, rather than crossing a single close-derived center. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, compute SMA10(high) and SMA10(low). Set SSL state bullish when close is above SMA10(high), bearish when close is below SMA10(low), otherwise carry the prior state; the conventional displayed lines swap with state. Enter next open on a bullish state transition while close is above EMA200. Exit in bearish state, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 173; future family-wise alpha <=0.05/173. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF hysteretic high/low average-channel family.  
DEFERRED: short cell, alternate windows/average types/filters, folds, holdout, Stage B and activation.
