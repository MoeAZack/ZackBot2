# Candidate 201 / Bullish Separating Lines preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C200 tree, refs, history, preregistrations, results and research tools were searched for Bullish Separating Lines; no implementation was found. Bullish engulfing, Kicker and other two-candle families are disclosed. This cell is distinct because opposite-colored candles must have matched opens within a fixed 0.10 ATR, and the bullish candle closes above the prior open; it neither gaps through the prior body nor opens below the prior close. One conventional cell only. All recorded sparse, near-miss and rejected results remain unchanged and are not rescued or tuned.

On completed 1h bars, require a bearish candle followed by a bullish candle whose open is within 0.10 current ATR of the prior open and whose close is above the prior open. Enter long next open when the bullish candle closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 201; future family-wise alpha <=0.05/201. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: distinct default-OFF two-candle matched-open continuation family.
DEFERRED: bearish counterpart, tolerance variants, folds, holdout, Stage B and activation.
