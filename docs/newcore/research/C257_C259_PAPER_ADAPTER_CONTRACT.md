# Qualified candidate execution adapter handoff

ROADMAP FIT: move independently qualified C257/C258/C259 economics into bounded PAPER/testnet cohorts under protected execution. This is the next Build slice after the causal runner handoff. Default OFF; no activation is authorized by this document alone.

## Existing seams verified on protected code

- `candidate_activation.py` freezes, preflights, enables and rolls back an already installed sleeve. It explicitly does not install strategies or supply candidate logic.
- `strategies.signals` returns independent per-symbol entry/exit arrays. `Engine.compute_signals` and `Engine.cycle` consume that interface.
- `Engine.cycle` currently admits each symbol independently and manages its exits independently. Registering two ordinary signals does not implement a paired candidate.
- `Engine.open_lot`, `_market_entry`, `_create_lot`, stop verification and reconciliation are the existing order/protection seams. Reuse their measured execution and safety behavior; do not bypass them with an unprotected order client.

## Required adapter behavior

1. Install explicit default-OFF candidate identities and immutable source/config versions. Keep the single-symbol C259 side books separate; a side cannot consume another side's cash, capacity or result. Keep unrelated legacy strategy lots managed.
2. Generate candidate intents from the accepted causal signal functions and a timestamped cohort snapshot. Preserve full point-in-time crypto membership followed by the correct filters, closed bars, funding availability, BTC reference, exact clocks and fixed indexed pairing/ranking. Do not apply the legacy rotation universe silently.
3. C257/C258 intents carry one attempt identity, both symbols/sides, declared book equity, aggregate rounded modeled risk, quantities, stops, source timestamp/digests, due exit and all admission limits. Validate/reserve the whole pair before submitting either leg. Two unrelated ordinary signals are insufficient.
4. Both accepted legs require independently confirmed exchange protection. Unknown second-leg or stop state creates a retained failed attempt with measured entry fills/costs; protect any accepted exposure and execute the frozen cleanup/fail-safe. Restart recovery must reconcile intent, fills and stops before new admissions, without duplicate submission.
5. Preserve each candidate's exact gap/range/open-exit/funding order, fixed exit and mate cleanup. Any testnet-vs-model difference is recorded explicitly; never silently replace candidate exits with the legacy generic runner, trailing stop or target rules.
6. Size from isolated candidate/side book capital, not the entire wallet. Reserve shared-account collateral/exposure across all books. A 2000 USD primary replay must not become a wallet-wide 1% risk order simply because the testnet wallet is larger. Diagnostic capital replays are observational and cannot change the primary book.
7. Preserve actual fill, fee, funding, spread/slippage, failure, timestamp and protection evidence under the original attempt. Pair-level net R and marked equity must reconcile to leg cashflows. Missing actual costs remain visible and cannot be fabricated as zero or cause an exposed attempt to disappear.
8. Integrate with generic activation only after the adapter is installed and source-bound. Activation repeats exact testnet endpoint/account checks, clean cohort boundary, economic acceptance, protection, risk-tier/budget and rollback gates. Routine testnet actions require no owner approval; unknown environment, credentials or real funds remain stop conditions.

## Bounded proof and acceptance

First use hostile fake venues: accepted first leg/failed second, uncertain second response, rejected stop, partial fill, duplicate response, crash between fill and persistence, restart with a missing stop, same-symbol competing intents, due-open stop collision, mate cleanup, stale inputs, insufficient shared collateral, actual-cost delay and rollback with open exposure. No self-acceptance.

Then run a bounded confirmed-testnet canary and rollback/restart drill on the accepted protected build, preserving exchange protection and durable evidence. Record exact strategy/build/config, account/environment identity without secrets, local Cairo timestamps, bounds and outcomes. A candidate economic failure parks that candidate; reusable adapter work may proceed for the next qualified candidate.

DEFERRED: strategy parameter tuning, new venue/platform, UI polish and mainnet. The separate owner mainnet approval remains mandatory.
