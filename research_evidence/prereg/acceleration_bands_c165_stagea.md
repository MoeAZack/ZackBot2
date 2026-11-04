# Candidate 165 / Acceleration Bands breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C164 tree, refs, history, preregistrations, results and research tools were searched for Acceleration Bands and Headley; no implementation was found. Correlation with STARC, Keltner, Bollinger, fixed-percentage envelopes and other channel breakouts is disclosed. Acceleration Bands are distinct because each bar's high/low is expanded by its normalized range before the expanded series are averaged. One conventional cell only. C164 is recorded as a positive near-miss rejected solely because 18.784330R drawdown breached the frozen 15R gate; it will not be tuned or rescued.

On completed 1h bars, compute conventional Headley Acceleration Bands over 20 bars: `width=4*(high-low)/(high+low)`, upper is SMA20 of `high*(1+width)`, lower is SMA20 of `low*(1-width)`, and mid is SMA20 of close. Enter next open when close crosses above the upper band while close is above EMA200. Exit when close is at/below the midline, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 165; future family-wise alpha <=0.05/165. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or TNET-01; holdout sealed.

ROADMAP FIT: distinct default-OFF normalized-range price-channel family.  
DEFERRED: short cell, alternate windows/factors/filters, folds, holdout, Stage B and activation.
