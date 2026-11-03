# Candidate 46 / equal-low liquidity-pool breakdown preregistration

Status: **FROZEN / NOT EVALUATED / RESEARCH ONLY**; frozen 3 November 2026 Cairo before results.  
Key: `equal_low_breakdown_short_1h`; 1h short only.

## Queue, ledger and duplicate identity

This is the unused short-side cell from the scout queue's deferred equal-highs/lows structure source. Before freezing,
the complete integrated Candidate 1–45 ledger/risk register, current tree, local/remote refs and tags, Git history,
preregistrations, results and research tools were searched for equal/repeated/clustered lows and liquidity-pool
breakdowns. Only the scout source's `equal highs/lows` phrase matched; no tested packet or result did. C45 is the
separately governed long cell above clustered highs. Donchian/C30 breaks any channel extreme without repeated support;
inside-bar/C32 uses a mother-bar pattern; liquidity-sweep/FVG is a false-break reversal. C46 instead requires separated
near-equal support touches and enters the first buffered close through that frozen level. It is not a rescue or tuning of
C45 or any earlier candidate.

## Frozen causal rule and costs

At completed decision bar `t`, inspect only lows from `t-24..t-1`. Freeze support as their minimum. A touch is a low
within 0.25 current ATR above support; require at least two touches whose lag positions are separated by >=3 bars. The
decision close must be the first close >=0.10 ATR below support (the prior close was not below its then-known 24-bar
edge), close <EMA50, and quote volume >=1.25 times its prior 48-bar median. A 2 ATR stop must be >=5x modeled round-trip
cost. Fill next open; exit above EMA20, at 2 ATR stop, 2R target or 16 bars. Costs are 5 bp taker +2 bp adverse slippage
per fill plus checksum-verified actual signed funding. One frozen cell; no parameter grid.

CORE-20 development `2024-10-14T00:00:00Z..2025-04-01T00:00:00Z`. Stage-A economic PASS requires pre-cost gross>0,
mean net R>0, PF>1.05, >=100 closes, >=8 symbols and >=20 closes in each BTC regime. Drawdown is reported/classified
under the prospective policy rather than used alone to rewrite economics. Candidate 46 increases the numbered
tested-candidate family to 46; any later promotion must use family-wise alpha <=`0.05/46` and chronological OOS.
PAPER eligibility remains BLOCK regardless of this development result. No rescue, folds, holdout, PAPER or runtime
action is authorized. The post-2026-09-28 holdout stays sealed.

ROADMAP FIT: genuinely unused short price-structure cell from the existing scout queue.  
DEFERRED: tolerance/spacing variants, folds, holdout, Stage B and all activation.
