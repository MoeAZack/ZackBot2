# OI-RECLAIM-01 result-blind preregistration

`funding_extreme_reclaim.v1.json` is the machine-authoritative draft. It fixes the candidate before any OI archive is
opened or any strategy return is computed. The economic claim is deliberately simple: extreme funding identifies a
crowded side; OI rebuilding plus a same-direction closed-price confirmation is the entry condition. Long and short are
separate claims, so one direction cannot hide the other's failure.

The loader supplies only rows whose normalized five-minute period end has aged by five minutes. Funding also ages by
five minutes. Price bars age by one minute. A decision is hourly at HH:05Z and executes only at the next 15-minute open.
Every OI, funding and price source requires an immutable manifest and hash before evaluation. There is no same-bar fill, missing-day
repair, forward fill, interpolation or silent universe shrink.

The frozen 36-month window has 18 months of development, five sequential three-month validation folds, and a final
three-month sealed holdout. The holdout may be opened once only after every validation gate passes. Costs include taker
fees, fixed base slippage, actual funding and 1.5x/2x stresses. The 35% research drawdown ceiling reflects the owner's
stated willingness to accept higher drawdown only when profitability and Calmar remain adequate; PAPER sizing is not
authorized by this contract.

Status is **DRAFT / no evaluation authority** until Codex signs an exact commit and independent review passes. A signed
manifest digest must then replace `identity.manifest_digest`; this metadata binding may not alter signal or gates.
