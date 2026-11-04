# Candidate 176 / Narrow Range 7 breakout preregistration

Status **FROZEN / NOT EVALUATED / RESEARCH ONLY**, 4 November 2026 Cairo. The complete C1-C175 tree, refs, history, preregistrations, results and research tools were searched for Narrow Range 7 and NR7; no implementation was found. C32 inside-bar breakout and other range-compression/breakout families are disclosed. This cell is distinct because the setup is the ordinal minimum high-low range across exactly seven completed bars and does not require geometric containment by the prior bar. One conventional cell only. C164 remains only a recorded positive near-miss rejected solely by its frozen drawdown gate; it is not rescued or tuned.

On completed 1h bars, mark a bar NR7 when its high-low range equals the minimum range of the trailing seven bars. On the immediately following completed bar only, enter next open if close is above the NR7 high and above EMA200. Exit when close is at/below SMA10, after 24 bars, at 2ATR stop or 2R target. The protection stop must cover at least 5x modeled 0.14% round-trip cost. Costs are 5bp taker plus 2bp adverse slippage per fill and actual signed funding.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. PASS requires gross>0, mean net R>0, PF>1.05, >=100 trades, >=8 symbols, >=20 trades in each BTC regime, and DD<=15R. Family 176; future family-wise alpha <=0.05/176. PAPER remains BLOCK. No tuning, rescue, folds, holdout, PAPER, runtime or BASE-CLEAN; holdout sealed.

ROADMAP FIT: distinct default-OFF ordinal range-compression breakout family.  
DEFERRED: short cell, alternate NR windows/entry persistence/exits, folds, holdout, Stage B and activation.
