"""S1 end to end on synthetic candles: entry -> fill -> protect -> signal-close (long, short), gapped stop, restart,
UNKNOWN / NOT_FOUND answers, reconciliation mismatch, strategy disabled."""
from decimal import Decimal as D

import pytest

from newcore.ports import header_of
from newcore.domain import (EntriesMode, IntentState, Lookup, ModeChanged, Ownership, Purpose, ReasonCode, ResultObserved,
                            ResultPhase)
from newcore.ports import EventKind
from newcore.runner import InjectedSignals, NoSignals
from slice_helpers import Crash, H4, World, flat_bars

SYM = 'SOLUSDT'
ENTRY_BAR, EXIT_BAR = 5, 10


def signals(side='LONG', entry=ENTRY_BAR, exit_=EXIT_BAR, more=None):
    sig = {(SYM, flat_bars(1)[0].open_ms + (entry + 1) * H4): (('enter', side),)}
    if exit_ is not None:
        sig[(SYM, flat_bars(1)[0].open_ms + (exit_ + 1) * H4)] = (('close', side),)
    sig.update(more or {})
    return InjectedSignals(sig, stop_atr=D('2'))


def kinds(w):
    return [header_of(e).kind.value for e in w.journal.read()]


def venue_orders(w):
    return [(o.order_type, o.position_side, o.reduce, o.status) for o in w.venue.orders_submitted()]


def assert_flat_known_empty(w):
    assert all(p.qty == 0 for p in w.venue.positions().value) and w.venue.open_orders().value == ()
    assert w.runner.portfolio().ownership is Ownership.KNOWN_EMPTY


@pytest.mark.parametrize('side,entry_px,stop_px,exit_px', [('LONG', '100.02', '98.02', '99.98'),
                                                            ('SHORT', '99.98', '101.98', '100.02')])
def test_entry_fill_protect_signal_close(side, entry_px, stop_px, exit_px):
    w = World(flat_bars(20), signals(side))
    w.run(ENTRY_BAR)
    lot, = w.runner.fold.open_lots()
    assert lot.avg_price == D(entry_px) and lot.qty == D('5')                 # 10 USDT risk / 2.0 stop distance
    stop = lot.live_stop
    assert stop.state is IntentState.WORKING and stop.intent.stop_price == D(stop_px)
    assert w.runner.portfolio().ownership is Ownership.KNOWN
    w.run(15)
    t, = w.runner.trades()
    assert (t.side, t.exit_code, t.exit_reason) == (side, 'SIGNAL_EXIT', ReasonCode.EXIT_SIGNAL)
    assert t.entry_price == D(entry_px) and t.exit_price == D(exit_px)
    assert t.fees == D('0.5') and t.gross == D('-0.2') and t.pnl == D('-0.7') and t.r == D('-0.07')
    assert t.entry_ms == w.candles[ENTRY_BAR + 1].open_ms and t.exit_ms == w.candles[EXIT_BAR + 1].open_ms
    assert kinds(w) == ['decision_recorded', 'intent_recorded', 'sent', 'result_recorded', 'intent_closed',  # entry
                        'decision_recorded', 'intent_recorded', 'sent', 'result_recorded', 'state_changed',  # stop
                        'decision_recorded', 'state_changed', 'result_recorded', 'intent_closed',  # cancel stop
                        'intent_recorded', 'sent', 'result_recorded', 'intent_closed']               # close
    assert venue_orders(w) == [('MARKET', side, False, 'FILLED'), ('STOP_MARKET', side, True, 'CANCELED'),
                               ('MARKET', side, True, 'FILLED')]
    assert_flat_known_empty(w)
    s = w.runner.summary()
    assert s.trades == 1 and s.counters['unprotected_cycles'] == 0 and s.mode == 'active'
    assert s.equity_end == D('500') - D('0.7') and s.ownership == 'known_empty'


@pytest.mark.parametrize('side,bar,exit_px,r', [('LONG', ('95', '95.2', '94.8', '95'), D('95') * D('0.9998'), '-2.56825025'),
                                                ('SHORT', ('105', '105.2', '104.8', '105'), D('105') * D('1.0002'),
                                                 '-2.57175025')])
def test_gapped_stop_fills_at_the_open(side, bar, exit_px, r):
    w = World(flat_bars(20, overrides={8: bar}), signals(side, exit_=None))
    w.run(15)
    t, = w.runner.trades()
    assert t.exit_code == 'STOP_HIT' and t.exit_price == exit_px and t.r == D(r)      # golden G-GAP-STOP-L/S-01
    assert t.exit_ms == w.candles[8].open_ms
    assert_flat_known_empty(w)


def _assert_one_entry_one_stop(w):
    kinds_ = [(o.order_type, o.reduce) for o in w.venue.orders_submitted()]
    assert kinds_.count(('MARKET', False)) == 1 and kinds_.count(('STOP_MARKET', True)) == 1


@pytest.mark.parametrize('after', range(1, 10))
def test_restart_mid_entry_cycle_no_duplicate_entry_or_stop(after):
    """Crash after every journal boundary of the entry cycle (decision, intent, sent, result, closed, stop decision,
    stop intent, stop sent, stop result)."""
    ref = World(flat_bars(20), signals())
    ref.run(15)
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.journal.fail_writes(1, after=after, error=Crash)
    with pytest.raises(Crash):                                   # the process dies here
        w.run(ENTRY_BAR)
    w.restart()                                                   # restart: fold the journal
    w.runner.cycle(w.close_ms(ENTRY_BAR))                        # the same candle is re-delivered
    _assert_one_entry_one_stop(w)
    assert w.runner.counters.redelivered == 1
    w.run(15)
    assert [(t.entry_price, t.exit_price, t.pnl) for t in w.runner.trades()] == \
        [(t.entry_price, t.exit_price, t.pnl) for t in ref.runner.trades()]
    _assert_one_entry_one_stop(w)
    assert_flat_known_empty(w)


@pytest.mark.parametrize('after', range(1, 8))
def test_restart_mid_exit_cycle_finishes_the_close_without_a_fresh_stop(after):
    """Crash points inside the signal-close cycle: after the close decision (1), the stop's cancelling (2), its result
    (3), its close (4), the close intent (5), its send (6), its result (7)."""
    ref = World(flat_bars(20), signals())
    ref.run(15)
    w = World(flat_bars(20), signals())
    w.run(EXIT_BAR - 1)
    w.journal.fail_writes(1, after=after, error=Crash)
    with pytest.raises(Crash):                                   # the process dies here
        w.run(EXIT_BAR)
    w.restart()
    w.runner.cycle(w.close_ms(EXIT_BAR))
    w.run(15)
    assert venue_orders(w) == venue_orders(ref)                  # entry, ONE stop (cancelled), ONE close
    assert [(t.exit_code, t.exit_price, t.pnl) for t in w.runner.trades()] == \
        [(t.exit_code, t.exit_price, t.pnl) for t in ref.runner.trades()]
    assert_flat_known_empty(w)


def test_restart_mid_trade_rebuilds_from_the_journal():
    ref = World(flat_bars(20), signals())
    ref.run(15)
    w = World(flat_bars(20), signals())
    w.run(7)
    events = len(w.journal.read())
    w.restart()
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING
    w.runner.cycle(w.close_ms(7))                                # re-run the last cycle: nothing new
    assert len(w.journal.read()) == events
    w.run(15)
    _assert_one_entry_one_stop(w)
    assert w.runner.trades()[0].pnl == ref.runner.trades()[0].pnl
    assert [header_of(e).digest for e in w.journal.read()] == [header_of(e).digest for e in ref.journal.read()]


def test_crash_between_decision_and_intent_resumes_only_inside_the_candle():
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.journal.fail_writes(1, after=1, error=Crash)                            # the decision lands, its intent does not
    with pytest.raises(Crash):                                   # the process dies here
        w.run(ENTRY_BAR)
    w.restart()
    w.runner.cycle(w.close_ms(ENTRY_BAR))                        # same candle: the derived intent is recorded + sent
    _assert_one_entry_one_stop(w)
    late = World(flat_bars(20), signals())
    late.run(ENTRY_BAR - 1)
    late.journal.fail_writes(1, after=1, error=Crash)
    with pytest.raises(Crash):                                   # the process dies here
        late.run(ENTRY_BAR)
    late.restart()
    late.run(12)                                                 # next candle: the signal is spent (fail closed)
    assert late.venue.orders_submitted() == ()


def test_unknown_entry_answer_holds_then_the_query_resolves_it():
    w = World(flat_bars(20), signals(more={(SYM, flat_bars(1)[0].open_ms + 13 * H4): (('enter', 'LONG'),)}))
    w.run(ENTRY_BAR - 1)
    w.venue.lose_next_market_answer('filled')
    w.run(ENTRY_BAR)
    f = w.runner.fold
    assert f.mode is EntriesMode.HOLD and ReasonCode.EXEC_ENTRY_UNCONFIRMED in f.mode_reasons
    entry = next(iv for iv in f.intents.values() if iv.purpose is Purpose.ENTRY)
    assert [r.phase for r in entry.results] == [ResultPhase.UNKNOWN, ResultPhase.FINAL]
    assert entry.state is IntentState.FILLED
    lot, = f.open_lots()
    assert lot.live_stop.state is IntentState.WORKING                     # protected in the same cycle
    _assert_one_entry_one_stop(w)
    w.run(11)                                                             # signal-close is allowed in HOLD
    assert w.runner.trades()[0].exit_code == 'SIGNAL_EXIT'
    w.run(13)                                                             # a new entry is not: SKIP (HOLD)
    skip = [d for d in f.decisions.values() if str(d.action) == 'skip']
    assert len(skip) == 1 and skip[0].reason is ReasonCode.RECONCILE_UNRECONCILED
    assert w.runner.resume(w.close_ms(13)) and f.mode is EntriesMode.ACTIVE
    resume = [e for e in w.journal.read() if isinstance(e, ModeChanged)][-1]
    assert resume.decision_id is not None and resume.reconciliation_id is not None


def test_not_found_alone_is_still_unknown():
    w = World(flat_bars(20), signals(exit_=None), strict=True)
    w.run(ENTRY_BAR - 1)
    entry_cid = None
    w.venue.lose_next_market_answer('filled')
    # the order's client id is derived from its key; learn it after the send by intercepting the first NOT_FOUND
    orig = w.venue.submit_market

    def submit(order):
        w.venue.not_found(order.ref.client_id, times=2)
        return orig(order)
    w.venue.submit_market = submit
    w.run(ENTRY_BAR)
    f = w.runner.fold
    entry = next(iv for iv in f.intents.values() if iv.purpose is Purpose.ENTRY)
    assert entry.state is IntentState.UNKNOWN                              # never assumed "not filled"
    assert entry.final is None and entry.results[-1].lookup is Lookup.NOT_FOUND
    assert f.open_lots() == [] and f.mode is EntriesMode.HOLD
    assert w.runner.counters.unprotected_cycles == 1                      # exposure the bot cannot own yet: HOLD
    w.run(ENTRY_BAR + 1)                                                  # second NOT_FOUND: nothing new recorded
    assert len(entry.results) == 2
    w.run(ENTRY_BAR + 2)                                                  # the order record appears: FINAL
    assert entry.state is IntentState.FILLED
    lot, = f.open_lots()
    assert lot.live_stop.state is IntentState.WORKING
    _assert_one_entry_one_stop(w)


def test_not_found_with_nothing_executed_is_unknown_until_corroborated_and_never_resent():
    """NOT_FOUND alone proves nothing: the entry stays UNKNOWN (owned work, HOLD) and is never re-sent; only two
    agreeing position reads after the visibility window + an explicit reconciliation decision resolve it."""
    w = World(flat_bars(20), signals(exit_=None, more={(SYM, flat_bars(1)[0].open_ms + 9 * H4): (('enter', 'LONG'),)}))
    w.run(ENTRY_BAR - 1)
    w.venue.lose_next_market_answer('not_filled')
    w.run(ENTRY_BAR)
    f = w.runner.fold
    entry = next(iv for iv in f.intents.values() if iv.purpose is Purpose.ENTRY)
    assert entry.state is IntentState.UNKNOWN and entry.final is None and f.mode is EntriesMode.HOLD
    assert w.runner.portfolio().ownership is Ownership.KNOWN                # the unknown entry is still owned work
    w.run(ENTRY_BAR + 1)                                                    # one read past the window: not enough
    assert entry.state is IntentState.UNKNOWN
    w.run(12)
    assert entry.state is IntentState.CANCELLED and str(entry.final.evidence) == 'not_found_corroborated'
    assert w.venue.orders_submitted() == () and f.mode is EntriesMode.HOLD    # nothing sent; HOLD until resume


def test_reconciliation_mismatch_holds_and_stops_entries():
    w = World(flat_bars(20), signals(entry=8, exit_=None), strict=True)
    w.run(5)
    assert w.runner.portfolio().ownership is Ownership.KNOWN_EMPTY
    w.venue.inject_position(SYM, 'LONG', D('3'), D('100'))
    w.run(6)
    f = w.runner.fold
    assert f.mode is EntriesMode.HOLD and ReasonCode.OWNERSHIP_UNTRACKED_POSITION in f.mode_reasons
    assert w.runner.portfolio().ownership is Ownership.UNKNOWN
    w.run(10)
    assert [str(d.action) for d in f.decisions.values()] == ['skip']
    assert w.venue.orders_submitted() == ()
    assert not w.runner.resume(w.close_ms(10))                             # still mismatched: stays in HOLD


def test_external_stop_cancel_is_restored():
    w = World(flat_bars(20), signals(exit_=None))
    w.run(ENTRY_BAR)
    stop = w.runner.fold.open_lots()[0].live_stop
    w.venue.external_cancel(stop.intent.client_order_id)
    w.run(ENTRY_BAR + 1)
    lot, = w.runner.fold.open_lots()
    assert [p.state for p in lot.protects] == [IntentState.CANCELLED, IntentState.WORKING]
    assert lot.protects[1].intent.reason is ReasonCode.PROTECT_RESTORING
    assert lot.protects[1].intent.stop_price == lot.protects[0].intent.stop_price
    assert w.runner.counters.unprotected_cycles == 0


def test_strategy_disabled_runs_without_trades():
    w = World(flat_bars(20), NoSignals())
    w.run(19, decide_last=False)
    assert w.journal.read() == () and w.venue.orders_submitted() == ()
    assert w.runner.trades() == [] and w.runner.counters.cycles == 20
    assert_flat_known_empty(w)


def test_replay_is_deterministic():
    a, b = World(flat_bars(20), signals()), World(flat_bars(20), signals())
    a.run(15)
    b.run(15)
    assert [header_of(e).digest for e in a.journal.read()] == [header_of(e).digest for e in b.journal.read()]


pytestmark = pytest.mark.usefixtures('journal_kind')            # every test: MemoryJournal and FileJournal
