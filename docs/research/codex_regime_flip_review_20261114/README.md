# Regime flip R6 — independent economic PASS and account-fit handoff

Research result head `10486f35dd513c51b375b3b72cfe5864763a7b39`, alpha `acc8d0eea5fbc964d45d582923732e2bbb5ba322`, cases `eb5f615c78660490e76cad97b8f09c2fffbe1e89`. **The frozen WIDE165 economic screen PASS is independently reproduced.** Accept as a candidate for bounded PAPER preparation, not mainnet approval or proof of durable profitability. No account/runtime/key/scheduler/trading action was taken.

## Independent proof

Two independent implementations rebuilt all6,075 raw signals,3,722 eligible, from166 pinned raw CSVs. Zero missing/extra. The separate signal review checked36 prefixes on12coins and all149 order0 entry clocks. R1 observes only completed daily closes; the UTC20:00 4h signal bar closes at next UTC00:00, and entry follows at that open. Cairo conversion follows the timestamp's IANA offset; no fixed offset is used.

The full independent simulator imports **neither Claude's strategy/runner nor the native execution engine**. It reconstructs ATR, R1/availability, D01, pending/capacity behavior, floor sizing, gaps/stops/signal/time exits, fees, slippage, funding and cash. All40 orderings match their published per-order statistics, and all149 order0 trade identities/reasons/cash match; maximum P&L error1.43e-14 dollars. Native Windows/CP1252 run passes. This uses the same disclosed10m-equity fixed-risk proxy for parity, not a real4,000-dollar account model.

Full165 medians:155closes, R15 +0.33170105, R23 +0.31230385, early +0.29887800, later +0.27387610, ex-best +0.17681732, fresh134 +0.22587814, extra31 +0.51608749. All frozen gates stand. All40 orders finish with one open modeled position; separately recorded liquidation marks at15bp range -1.76 to +24.90 dollars. These open marks are not counted as closes or used to rescue the gates.

## Concentration, frequency and units

This is a slower trend complement: order0 is149 closes over roughly214weeks (about0.70/week). It is not the frequent-trading basket by itself. Profit is concentrated in large winners: removing the best3coins leaves median +0.007832R15; removing the best5 gives -0.089417. These are post-hoc diagnostics, not extra gates. One pass in38LONGSCOUT candidates and consumed calendar data limit confidence. No tuning or parameter rescue.

Order0 realized P&L at15bp is +306.594129 dollars = +38.324266 fixed-$8 R15, not the native/base-cost +40.9R figure used in the source README. The side sums are +26.710R15 long and +11.614R15 short. Closed drawdown18.145697R15 is about145.17dollars/3.63% at the8-dollar/4,000-dollar reporting scale; this is not marked-account DD. Native pooled median trade -0.393R and maximum +32.048R should not be labelled R15: repriced pooled values are -0.420R15 and +31.998R15. Pooling orders repeats market episodes, not independent trades. High DD is reported, not a veto.

## Objectively covered testnet subset

Read-only official demo-fapi exchangeInfo shows163/165contracts tradable; ICPUSDT and TLMUSDT unavailable. I froze R6-TNET163-FIT-D01 at7f42f2cf, then pinned the independent runner before its first outcome at7a81c7fd. Source/management/gates remain unchanged; only the objectively venue-covered163list and actual current testnet filters differ. No profit-based exclusion. This is one dependent deployment-fit trial, not a new independent strategy. Full165 evidence is retained.

Independent40-order fit simulation also **FIT_PASS**: median150.5closes, R15 +0.32863186, R23 +0.31023155, early +0.40518823, later +0.29199789, ex-best +0.18720087, fresh132 +0.24473130, extra31 +0.61675029; order0 86/64closes per half. All gates pass. Native/Claude confirmation of this fit variant remains a gate before activation because Codex authored the fit simulator/variant. End positions and marks are recorded separately. These fit trades are not claimed to match the165trade tape. Fit freeze/rules/source: ../regime_flip_testnet_fit_20261114/.

## Integration decision

GO to a small separate default-OFF R6-PAPER-01 implementation and protection/account-fit proofs. No daily-timeframe support project is needed: this rule consumes4hdata and builds daily state internally. Keep #343 parked. Preserve other sleeves and all other accounts; no broad runtime upgrade. Existing account4 image is custom/pre-#333 with the CLOCK-1021 backport, so an eventual overlay must be reviewed against that exact image.

Concrete remaining gates: candidate-specific163symbol admission (existing API rejects lists over80, global universe caps120); no silent universe truncation or profit-based dropping; exact closed1hD01 eligibility/availability; real book-mode sizing receipt demonstrating at-most8dollar risk and3333.33aggregate notional cap; testnet-only registration, book mapping, exchange protection and quantity coverage, restart/state/rollback safety, independent exact-head checks and required CI. Do not merely infer sizing from share/risk labels. No owner relay is required for routine authorized testnet preparation, but no account action is authorized by this research receipt itself.

Targets on14November Cairo: first default-OFF code/proof handoff12:00, independent decision14:00, earliest protected account4 canary18:00 if all gates pass. These are work/deployment targets, not promises of future profit. Claude continues the next frozen candidate batch while this lead advances; neither lane waits for daily rows.
