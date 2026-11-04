# Candidate 235 / Identical Three Crows preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C234 tree, refs, history, preregistrations, results and research tools were searched for Identical Three Crows; no implementation was found. C214 Three Black Crows, C233 Upside Gap Two Crows, C234 Two Crows and other bearish multi-candle families are disclosed. This is a distinct fixed continuation hypothesis: three bearish candles close successively lower and each later candle opens within 0.10 ATR of the prior close. Unlike C214's broader nested-open geometry, this conventional family specifically requires nearly identical handoff levels. It was selected independently, not as a rescue or alteration of C234's zero-trade rejection or any prior result. One cell only.

On completed 1h bars, require three consecutive bearish candles with strictly descending closes. Candle 2's open must be within 0.10 of candle 2 ATR from candle 1's close; candle 3's open must be within 0.10 of candle 3 ATR from candle 2's close. Enter short next open when candle 3 closes below EMA200. Exit when close is at/above SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 235; future family-wise alpha <=0.05/235. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime, mainnet or BASE-CLEAN work; holdout sealed. Undefined zero-trade statistics fail closed.

ROADMAP FIT: next distinct default-OFF conventional three-candle bearish continuation family.
DEFERRED: variants, trend/location/body-size/follow-through filters, folds, holdout, Stage B and activation.
