# Candidate 170 / Range Action Verification Index preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C169 tree, refs, history, preregistrations, results and research tools were searched for Range Action Verification Index, RAVI and Chande RAVI; no implementation was found. Correlation with moving-average spread, PPO and trend-strength families is disclosed. This cell is distinct because conventional RAVI uses the absolute percentage separation of fixed SMA7 and SMA65 with a fixed 3% trend threshold, while direction is supplied separately by the fast/slow ordering. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h closes, compute `RAVI=100*abs(SMA7-SMA65)/SMA65`. Enter next open when RAVI crosses above 3%, SMA7 is above SMA65, and close is above EMA200. Exit when RAVI is at/below 3% or SMA7 is at/below SMA65, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 170; future family-wise alpha <=0.05/170. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF long-horizon SMA-spread trend-strength family.  
DEFERRED: short cell, alternate windows/thresholds/filters, folds, holdout, Stage B and activation.
