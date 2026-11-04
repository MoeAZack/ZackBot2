# Candidate 239 / Bearish Closing Marubozu preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C238 tree, refs, history, preregistrations, results and research tools were searched for bearish Closing Marubozu; no implementation was found. C194 bearish Marubozu, C198 Bearish Belt Hold, C238 bullish Closing Marubozu and other one-candle momentum families are disclosed. This is the conventional short-side counterpart but a separate fixed hypothesis: a dominant bearish real body has a near-zero lower/closing-side shadow while its upper shadow may remain, whereas C194 requires both shadows near zero and C198 constrains the upper/opening-side shadow. It was selected independently, not as a rescue or alteration of C238's broad negative rejection or any prior result. One cell only.

On completed 1h bars, require a bearish candle whose lower shadow is no more than 0.10 of its real body and whose real body is at least 0.60 of the full high-low range. The upper shadow is otherwise unrestricted. Enter short next open when the completed candle closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 239; future family-wise alpha <=0.05/239. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime, mainnet or BASE-CLEAN work; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: next distinct default-OFF conventional one-candle bearish continuation family.
DEFERRED: variants, folds, holdout, Stage B and activation.
