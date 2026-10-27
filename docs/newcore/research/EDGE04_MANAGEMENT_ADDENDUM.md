# EDGE-04 management addendum

Status: **frozen before market-data access or any EDGE-04 result, 26 October 2026 Cairo**. The machine-readable
authority is `EDGE04_MANAGEMENT_ADDENDUM_v1.json`; it supplements, and does not rewrite, the original preregistration.

H1 uses the existing short runtime management: initial stop 2 ATR, half off at +1R, breakeven only after that partial
actually executes, remaining target +3R, and a 60-bar cap. H3 uses its 2.5 ATR stop, full +3R target, genuine signal
exit and 180-bar cap. H2 and H4 retain their genuine signal exits and have no invented time exit. They are observed
for at most 180 bars; a still-open position becomes `right_censored_runtime_no_cap`, makes the row incomplete, and is
neither force-closed nor silently excluded.

An open through a stop exits at that open before funding or targets. If a non-gap stop and a target share an unresolved
4h candle, the stop wins. Otherwise targets execute in favourable price order. Every executed slice pays the frozen
taker fee and slip-v1 slippage with adverse tick rounding; funding uses actual events, causal marks, and the quantity
held at each event. Without finer ordered-trade evidence, a non-gap target is accounted at the 4h bar close, so actual
funding events earlier in that bar apply to the pre-target quantity. A partial floored to zero by `stepSize` changes no
state and cannot move the stop to breakeven.

ROADMAP FIT: EDGE-04 execution-grade development evaluation.

DEFERRED: market-result execution, sealed holdout, PAPER/runtime changes, mainnet, and new variants.
