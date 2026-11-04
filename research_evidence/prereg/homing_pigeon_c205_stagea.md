# Candidate 205 / Homing Pigeon preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C204 tree, refs, history, preregistrations, results and research tools were searched for Homing Pigeon; no implementation was found. C186 Bullish Harami, C203 Matching Low and other two-candle contraction families are disclosed. This cell is distinct because both candles must be bearish and the second real body must be strictly contained inside the first; Harami uses an opposite-colored second body and Matching Low requires matched closes. One conventional cell only. All recorded sparse, near-miss and rejected results remain unchanged and are not rescued or tuned.

On completed 1h bars, require two consecutive bearish candles. The second open must be strictly below the first open and the second close strictly above the first close, fully containing the second bearish real body inside the first. Enter long next open when the second candle closes above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 205; future family-wise alpha <=0.05/205. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: distinct default-OFF two-candle same-color body-contraction family.
DEFERRED: body-size/location variants, folds, holdout, Stage B and activation.
