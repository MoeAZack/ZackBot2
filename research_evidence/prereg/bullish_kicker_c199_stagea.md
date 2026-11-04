# Candidate 199 / Bullish Kicker preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C198 tree, refs, history, preregistrations, results and research tools were searched for Bullish Kicker; no implementation was found. Bullish engulfing, belt-hold and other two-candle reversal families are disclosed. This cell is distinct because the bullish candle must open at or above the prior bearish candle's open, gapping through the entire prior real body; it does not merely engulf or recover that body intrabar. One conventional cell only. All recorded sparse, near-miss and rejected results remain unchanged and are not rescued or tuned.

On completed 1h bars, require a bearish candle followed by a bullish candle; each real body must be at least 60% of its own high-low range, and the bullish candle must open at or above the prior bearish open. Enter long next open when the bullish candle closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 199; future family-wise alpha <=0.05/199. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF two-candle gap reversal family.
DEFERRED: bearish counterpart, body/gap variants, folds, holdout, Stage B and activation.
