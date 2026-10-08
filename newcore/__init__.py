"""NEWCORE: the replacement trading core (docs/NEWCORE_EXECUTION_PLAN.md).

It is new code in a new package. It never imports the legacy modules (engine, backtest, app, grid, ...) and never reads
legacy state: NEWCORE starts flat on testnet. `newcore.domain` (NC-01) is the root of the dependency graph and is pure
stdlib with no IO, clock, randomness or network.
"""
