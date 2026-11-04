# Candidate 159 / Volume-Weighted Moving Average preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C158 tree, refs, history, preregistrations, results and research tools were searched for Volume-Weighted Moving Average and VWMA; no implementation was found. Correlation with volume-price indicators and SMA/WMA/SMMA/TMA/EMA-family baselines is disclosed. VWMA uniquely weights each trailing close by its contemporaneous base-asset volume, without cumulative anchoring or directional volume signs. One conventional cell only.

On completed 1h bars, compute conventional VWMA(20) as `sum(close*base_volume)/sum(base_volume)` across the trailing 20 bars. Enter next open when close crosses above VWMA while close is above EMA200. Exit when close is at/below VWMA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 159; future family-wise alpha <=0.05/159. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF rolling volume-weighted-average family.  
DEFERRED: short cell, alternate lengths/volume fields/filters, folds, holdout, Stage B and activation.
