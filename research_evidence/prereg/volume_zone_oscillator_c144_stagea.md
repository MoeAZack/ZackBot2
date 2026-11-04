# Candidate 144 / Volume Zone Oscillator preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C143 tree, refs, history, preregistrations, results and research tools were searched for Volume Zone Oscillator, VZO and EMA signed-volume ratios; no implementation was found. Correlation with VPI, OBV, PVT, CMF, Force Index, Klinger and Chaikin Oscillator is disclosed. VZO uniquely normalizes EMA-smoothed direction-signed volume by EMA-smoothed total volume. One conventional cell only.

On completed 1h bars, sign base volume by the sign of close-to-close change, treating unchanged closes as zero. Conventional VZO(14) is `100*EMA14(signed volume)/EMA14(volume)`. Enter next open when VZO crosses above zero and close is above EMA20. Exit at VZO<=0, 24 bars, 2ATR stop or 2R target. The stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding. Zero/invalid denominator cannot qualify.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 144; future family-wise alpha <=0.05/144. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER or runtime; holdout sealed.

ROADMAP FIT: distinct default-OFF normalized signed-volume momentum family.  
DEFERRED: short cell, alternate spans/thresholds/filters, folds, holdout, Stage B and activation.
