"""Cowork's S1 crash-safety findings on 30c9ce5 (each repro below is a regression test that failed there) and the crash
matrix BETWEEN journal writes and venue calls: the process dies just before every venue effect (its 'sent' / 'cancelling'
already journaled) and just after it (the venue executed, the answer never journaled), for entry, stop, replacement and
close, long and short. After every crash + restart:
  - never ACTIVE with exposure lacking a confirmed stop at a cycle end (strict invariants raise otherwise), and no
    unprotected HOLD cycle either;
  - no duplicate orders (one entry per signal, at most one resting stop per position at any time);
  - an eventual safe state: flat (KNOWN_EMPTY) or protected."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, Evidence, IntentState, Lookup, Purpose, ReasonCode
from newcore.risk import BookPolicy
from newcore.runner import InjectedSignals
from slice_helpers import H4, Crash, World, flat_bars

SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
ENTRY_BAR, EXIT_BAR, LAST = 5, 10, 15


def signals(side='LONG', exit_=EXIT_BAR):
    s = {(SYM, T0 + (ENTRY_BAR + 1) * H4): (('enter', side),)}
    if exit_ is not None:
        s[(SYM, T0 + (exit_ + 1) * H4)] = (('close', side),)
    return InjectedSignals(s, stop_atr=D('2'))


def run_safely(w, upto, hooks=None, open_stops=None):
    """Cycle to `upto`; on Crash: restart (journal reopened, gate rebuilt), re-deliver the same candle, go on."""
    hooks = hooks or {}
    start = max(0, (w.venue.now_ms - w.t0) // H4)
    for i in range(start, upto + 1):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        if i in hooks:
            hooks[i](w)
        try:
            w.runner.cycle(t)
        except Crash:
            w.port.disarm()
            w.restart()
            w.runner.cycle(t)
        if open_stops is not None:
            n = sum(1 for o in w.venue.open_orders().value if o.order_type == 'STOP_MARKET')
            open_stops.append(n)
    return w.runner


def stops_of(w):
    return [o for o in w.venue.orders_submitted() if o.order_type == 'STOP_MARKET']


def entries_of(w):
    return [o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and not o.reduce]


def flat(w):
    return all(p.qty == 0 for p in w.venue.positions().value) and not w.venue.open_orders().value


# ================================================================================================ the six findings
def test_1_stop_send_crash_is_resent_with_the_same_client_id_never_active_naked():
    """30c9ce5: the stop stayed UNKNOWN (NOT_FOUND), nothing re-sent, ACTIVE with 5 long and no stop."""
    w = World(flat_bars(20), signals(exit_=None))
    w.run(ENTRY_BAR - 1)
    w.port.crash(2, 'before')                                     # effect 2 = the protective stop
    run_safely(w, ENTRY_BAR + 2)
    lot, = w.runner.fold.open_lots()
    stop = lot.live_stop
    assert stop.state is IntentState.WORKING and len(lot.protects) == 1   # the SAME intent, re-sent
    assert [r.lookup for r in stop.results[:1]] == [Lookup.NOT_FOUND]
    s, = stops_of(w)
    assert s.ref.client_id == stop.intent.client_order_id and s.status == 'NEW'
    assert w.runner.fold.mode is EntriesMode.ACTIVE and w.runner.counters.unprotected_cycles == 0


def test_2_close_send_crash_after_the_stop_cancel_completes_the_close():
    """30c9ce5: stop cancelled, close SUBMITTED but never sent -> HOLD, naked, nothing completed the close."""
    w = World(flat_bars(20), signals())
    w.run(EXIT_BAR - 1)
    w.port.crash(4, 'before')                                     # 1 entry, 2 stop, 3 cancel, 4 close
    run_safely(w, LAST)
    assert flat(w)
    t, = w.runner.trades()
    assert t.exit_code == 'SIGNAL_EXIT' and w.runner.counters.unprotected_cycles == 0
    assert len([o for o in w.venue.orders_submitted() if o.reduce and o.order_type == 'MARKET']) == 1


def test_2b_a_close_that_cannot_complete_restores_a_stop_in_hold():
    w = World(flat_bars(20), signals())
    w.run(EXIT_BAR - 1)
    w.port.crash(4, 'before')
    w.port.refuse('reduce')                                       # the re-sent close is refused too
    run_safely(w, EXIT_BAR + 1)
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert w.runner.counters.unprotected_cycles == 0


def test_3_entry_send_crash_resolves_by_corroboration_then_resume_succeeds():
    """30c9ce5: entry UNKNOWN forever, HOLD sticky, resume() False forever."""
    w = World(flat_bars(30), signals(exit_=None), strict=True)
    w.run(ENTRY_BAR - 1)
    w.port.crash(1, 'before')
    run_safely(w, ENTRY_BAR)
    f = w.runner.fold
    entry = next(iv for iv in f.intents.values() if iv.purpose is Purpose.ENTRY)
    assert entry.state is IntentState.UNKNOWN and f.mode is EntriesMode.HOLD
    assert not w.runner.resume(w.close_ms(ENTRY_BAR))
    run_safely(w, ENTRY_BAR + 3)                                  # two agreeing reads past the visibility window
    assert entry.state is IntentState.CANCELLED and entry.final.evidence is Evidence.NOT_FOUND_CORROBORATED
    assert len(entry.final.corroboration) >= 2 and entry.final.resolved_by is not None
    assert entries_of(w) == [] and flat(w)
    assert w.runner.resume(w.close_ms(ENTRY_BAR + 3)) and f.mode is EntriesMode.ACTIVE


def test_3b_a_lost_entry_that_did_fill_is_adopted_and_protected():
    w = World(flat_bars(30), signals(exit_=None))
    w.run(ENTRY_BAR - 1)
    orig = w.venue.submit_market

    def submit(order):                                           # executes, the answer is lost, and the order
        w.venue.not_found(order.ref.client_id, times=99)          # record never becomes readable
        w.venue.lose_next_market_answer('filled')
        return orig(order)
    w.venue.submit_market = submit
    run_safely(w, ENTRY_BAR + 4)
    f = w.runner.fold
    entry = next(iv for iv in f.intents.values() if iv.purpose is Purpose.ENTRY)
    assert entry.state is IntentState.FILLED and entry.final.evidence is Evidence.POSITION_ADOPTED
    lot, = f.open_lots()
    assert lot.qty == D('5') and lot.live_stop.state is IntentState.WORKING
    assert len(entries_of(w)) == 1


def test_4_stop_and_close_both_refused_is_bounded_never_recursion():
    """30c9ce5: _send_stop / _close_lot / _send_close / _protect recursed until RecursionError."""
    w = World(flat_bars(20), signals(exit_=None), strict=False)
    w.port.refuse('stop')
    w.port.refuse('reduce')
    w.run(ENTRY_BAR + 1)                                          # no RecursionError
    r = w.runner
    assert r.fold.mode is EntriesMode.HOLD and ReasonCode.EXEC_STOP_FAILED in r.fold.mode_reasons
    assert r.counters.incidents >= 1 and r.incidents
    assert len(w.port.effects) <= 1 + 2 * 3 * 2                   # bounded per cycle: 2 rounds of stop + close


def test_5_duplicate_client_id_means_the_order_exists():
    """A duplicate-id rejection is never 'refused, nothing executed': query by cid and adopt the record."""
    w = World(flat_bars(20), signals(exit_=None))
    w.run(ENTRY_BAR - 1)
    w.port.crash(2, 'after')                                      # the stop landed, the answer was lost
    with pytest.raises(Crash):
        w.run(ENTRY_BAR)
    stop, = stops_of(w)
    w.venue.not_found(stop.ref.client_id, 1)                      # the restart's first query does not see it yet
    w.restart()
    w.runner.cycle(w.close_ms(ENTRY_BAR))                         # NOT_FOUND -> re-send -> duplicate id -> query
    w.run(ENTRY_BAR + 1)
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING and len(lot.protects) == 1
    assert all(r.evidence is not Evidence.EXCHANGE_REFUSED for r in lot.protects[0].results)
    assert len(stops_of(w)) == 1 and w.runner.fold.mode is EntriesMode.ACTIVE


def test_6_leverage_cap_holds_at_the_fill_through_a_gap():
    """30c9ce5: sized at the signal close, a 10% gap at the entry open gave 3.30x. The book sizes the cap on the
    signal close x (1 + cap_gap_buffer) (0.10 by default): a 10% gap stays within 3x."""
    from test_book_s4 import Book, SYMS
    gap = {s: {6: ('109.9', '110.5', '109.5', '110')} for s in SYMS[:4]}           # +9.9%: + slip stays < 10%
    sig = {(s, 5): (('enter', 'LONG'),) for s in SYMS[:4]}
    b = Book(sig, overrides=gap, policy=BookPolicy(risk_pct=D('0.02'), max_positions=None, daily_loss_pct=None,
                                                   kill_drawdown_pct=None))
    b.run(6)
    gross = sum(x.qty * x.avg_price for x in b.runner.fold.open_lots())      # at the (gapped) fill prices
    assert D('1400') < gross <= 3 * D('500')                      # 3 x the decision-time equity, not 3.30x
    assert b.runner.counters.cap_exceeded == 0
    unbuffered = Book(sig, overrides=gap, policy=BookPolicy(risk_pct=D('0.02'), max_positions=None,
                                                            daily_loss_pct=None, kill_drawdown_pct=None,
                                                            cap_gap_buffer=D(0)))
    unbuffered.run(6)                                             # without the buffer: surfaced, never silent
    assert unbuffered.runner.counters.cap_exceeded == 1 and unbuffered.runner.incidents


# ================================================================================================ the crash matrix
def scenario(kind, side):
    """kind: 'trade' (entry, stop, cancel, close), 'replacement' (an external cancel forces a restored stop),
    'stopped' (the stop fills on a gap)."""
    if kind == 'stopped':
        down = ('95', '95.2', '94.8', '95') if side == 'LONG' else ('105', '105.2', '104.8', '105')
        return World(flat_bars(20, overrides={8: down}), signals(side, exit_=None)), {}
    hooks = {}
    if kind == 'replacement':
        def kill_stop(w):
            for lot in w.runner.fold.open_lots():                 # (none when the crash stopped the entry)
                if lot.live_stop is not None:
                    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
        hooks[7] = kill_stop
    return World(flat_bars(20), signals(side)), hooks


def effects_of(kind, side):
    w, hooks = scenario(kind, side)
    run_safely(w, LAST, hooks)
    return len(w.port.effects), w


MATRIX = [(k, s, n, when) for k in ('trade', 'replacement', 'stopped') for s in ('LONG', 'SHORT')
          for n in range(1, effects_of(k, s)[0] + 1) for when in ('before', 'after')]


@pytest.mark.parametrize('kind,side,n,when', MATRIX)
def test_crash_between_journal_and_venue(kind, side, n, when):
    _, ref = effects_of(kind, side)
    w, hooks = scenario(kind, side)
    w.port.crash(n, when)
    stops = []
    run_safely(w, LAST, hooks, open_stops=stops)                  # strict: ACTIVE + unprotected raises
    r = w.runner
    assert r.counters.unprotected_cycles == 0                     # not even in HOLD
    assert max(stops) <= 1                                        # never two resting stops
    assert len(entries_of(w)) <= 1
    if r.fold.open_lots():
        lot, = r.fold.open_lots()
        assert lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    else:
        assert flat(w)
    if not (n == 1 and when == 'before'):                         # the entry reached the venue: same trade
        assert [(t.exit_code, t.entry_price, t.exit_price) for t in r.trades()] == \
            [(t.exit_code, t.entry_price, t.exit_price) for t in ref.runner.trades()]
