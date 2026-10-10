"""NEWCORE slice S1 runner: one loop for replay now and live later.

    runner    Runner / RunnerConfig: sync, reconcile, protect, decide, invariants (one cycle per closed candle)
    fold      the state as a fold of the journal (restart = fold)
    signals   SignalSource: EmaMomSignals (newcore.strategy), InjectedSignals (goldens), NoSignals
    sizing    fixed-fractional risk over the stop distance, leverage cap, step floor
    records   NC-01 record builders, VenuePort outcome -> OrderResult
    outcome   TradeOutcome per closed lot + run summary (projection from journal + venue fills / funding)
    ids       restart-stable ids beyond step 0
    replay    the candle-clock driver over FakeVenue
"""
from .outcome import RunSummary, TradeOutcome
from .replay import run_replay
from .runner import InvariantBreach, Reconciliation, Runner, RunnerConfig
from .signals import EmaMomSignals, InjectedSignals, NoSignals, Signal
from .sizing import SizingPolicy, size_entry
