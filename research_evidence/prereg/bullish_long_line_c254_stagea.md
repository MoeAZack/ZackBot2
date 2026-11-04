# Candidate 254 / Bullish Long Line preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C253 tree, refs, history, preregistrations, results and research tools were searched for Bullish Long Line; no implementation was found. C193 Bullish Marubozu, C197 Bullish Belt Hold, C238 Bullish Closing Marubozu and other dominant-body candle families are disclosed. This candidate is distinct because neither shadow is individually constrained: only the bullish real body must dominate the complete candle range. One fixed conventional cell only.

On completed 1h bars, require a strictly bullish candle with positive high-low range and real body at least 80% of that range. Enter long next open when the candle closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 254; future family-wise alpha <=0.05/254. Default-OFF; PAPER eligibility remains BLOCK. No tuning, rescue, folds, holdout, PAPER activation, runtime, mainnet or BASE-CLEAN work; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: next distinct default-OFF non-gap dominant-body bullish continuation family.
DEFERRED: bearish counterpart, threshold variants, folds, holdout, Stage B and activation.
