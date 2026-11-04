# Candidate 143 / Chaikin Oscillator preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C142 tree, refs, history, preregistrations, results and research tools were searched for Chaikin Oscillator and EMA momentum of the accumulation/distribution line; no implementation was found. Correlation with C128 Chaikin Money Flow, OBV, PVT, Force Index and Klinger is disclosed. This cell uniquely takes the conventional EMA3-minus-EMA10 momentum of cumulative close-location volume. One conventional cell only.

On completed 1h bars, compute the conventional money-flow multiplier `((close-low)-(high-close))/(high-low)`, multiply by base volume and cumulatively sum to the accumulation/distribution line. The Chaikin Oscillator is EMA3(ADL)-EMA10(ADL). Enter next open when it crosses above zero and close is above EMA20. Exit at oscillator<=0, 24 bars, 2ATR stop or 2R target. The stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding. Zero-range bars contribute zero.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 143; future family-wise alpha <=0.05/143. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER or runtime; holdout sealed.

ROADMAP FIT: distinct default-OFF cumulative close-location-volume momentum family.  
DEFERRED: short cell, alternate spans/thresholds/filters, folds, holdout, Stage B and activation.
