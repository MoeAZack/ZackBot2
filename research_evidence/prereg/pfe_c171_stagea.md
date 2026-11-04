# Candidate 171 / Polarized Fractal Efficiency preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C170 tree, refs, history, preregistrations, results and research tools were searched for Polarized/Polarised Fractal Efficiency and PFE oscillator; no implementation was found. Correlation with efficiency-ratio, fractal-dimension, roughness and trend oscillators is disclosed. This cell is distinct because it compares the signed Euclidean displacement over ten bars with the full Euclidean one-bar path and then smooths that path efficiency. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h closes, compute conventional PFE(10): signed `100*sqrt((close-close[10])^2+10^2) / sum_10(sqrt((close-close[1])^2+1))`, then EMA-smooth it over five bars. Enter next open when smoothed PFE crosses above zero while close is above EMA200. Exit when smoothed PFE is at/below zero, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 171; future family-wise alpha <=0.05/171. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF signed path-efficiency trend family.  
DEFERRED: short cell, alternate horizons/smoothing/thresholds, folds, holdout, Stage B and activation.
