# Candidate 168 / Fractal Chaos Bands breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C167 tree, refs, history, preregistrations, results and research tools were searched for Fractal Chaos Bands and confirmed fractal-pivot channels; no implementation was found. Correlation with Donchian/channel breakouts, confirmed swing logic, C110 FRAMA and C63 multiscale roughness is disclosed. This cell is distinct because its channel holds only the latest causally confirmed five-bar Williams fractal high/low; it does not use a rolling extreme or fractal-dimension estimate. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, confirm a fractal high/low only after two bars to its right have closed: the center bar must be the maximum high/minimum low across two left bars, itself and two right bars. Forward-fill the latest confirmed fractal high as the upper band and low as the lower band. Enter next open when close crosses above the current upper band while close is above EMA200. Exit when close is at/below the current lower band, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 168; future family-wise alpha <=0.05/168. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime, TNET-01 or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF causally confirmed pivot-channel family.  
DEFERRED: short cell, alternate pivot widths/filters, folds, holdout, Stage B and activation.
