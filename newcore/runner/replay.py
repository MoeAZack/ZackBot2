"""Replay driver: the candle clock for FakeVenue + Runner (live replaces it with a wall clock and real klines).

For every candle close t in (start_ms, end_ms], in order:
    venue.advance_to(t)     the venue plays the candle that just closed (resting stops, funding)
    runner.cycle(t)         sync, reconcile, protect, decide, reconcile + invariants
The cycle at end_ms runs without decisions: an order sent there would have no next open to fill at.
`restart_at` (a candle close) swaps in a fresh Runner built by `make_runner()` over the SAME journal and venue just
before that cycle, which re-delivers that candle's signals to a runner that only knows the journal.
"""
from __future__ import annotations


def run_replay(runner, venue, *, start_ms, end_ms, tf_ms, make_runner=None, restart_at=(), on_cycle=None):
    restart_at = set(restart_at)
    t = start_ms + tf_ms
    while t <= end_ms:
        venue.advance_to(t)
        if t in restart_at:
            runner = make_runner()
        rec = runner.cycle(t, decide=t < end_ms)
        if on_cycle is not None:
            on_cycle(runner, t, rec)
        t += tf_ms
    return runner
