# Candidate 149 / VIDYA preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C148 tree, refs, history, preregistrations, results and research tools were searched for VIDYA, Variable Index Dynamic Average and CMO-weighted adaptive averages; no implementation was found. Correlation with C110 FRAMA, C117 KAMA, C123 McGinley and other moving-average cells is disclosed. VIDYA uniquely scales its recursive update coefficient by the absolute fixed-window CMO ratio. One conventional cell only.

On completed 1h closes, compute conventional VIDYA length 14 with absolute CMO9 as its volatility/momentum index: update coefficient `2/(14+1)*abs(CMO9)/100`; missing warm-up has coefficient zero. Enter next open when close crosses above VIDYA while close is above EMA200. Exit when close is at/below VIDYA, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 149; future family-wise alpha <=0.05/149. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or REC-02; holdout sealed.

ROADMAP FIT: distinct default-OFF CMO-weighted adaptive-average family.  
DEFERRED: short cell, alternate lengths/momentum windows/filters, folds, holdout, Stage B and activation.
