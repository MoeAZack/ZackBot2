"""Step-0 r3 / Codex rulings on the Runner:
- JournalUnavailable -> hard HOLD: no decision, no send, no journal write until a restart; surfaced, never raised.
- Opening risk is keyed: an unkeyed ENTER / ADD is refused at the runner boundary; a duplicate candle or a re-delivered
  signal (same key) never creates a second ENTER / ADD, also across a restart."""
from decimal import Decimal as D

import pytest

from newcore.domain import Action, Authority, Purpose, ReasonCode
from newcore.ports import JournalConflict, claim_signal
from newcore.runner import InjectedSignals
from newcore.runner import ids
from newcore.runner.records import planned_intent
from newcore.runner.runner import UnkeyedOpeningDecision
from slice_helpers import H4, World, flat_bars

SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms


def sig(**bars):
    """bars: {'enter5': 'LONG', ...} -> injected signals at those candle closes."""
    out = {}
    for name, side in bars.items():
        action, bar = ('enter', int(name[5:])) if name.startswith('enter') else ('close', int(name[5:]))
        out.setdefault((SYM, T0 + (bar + 1) * H4), []).append((action, side))
    return InjectedSignals({k: tuple(v) for k, v in out.items()}, stop_atr=D('2'))


def entries(w):
    return [o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and not o.reduce]


# ------------------------------------------------------------------------------------------------ hard HOLD
@pytest.mark.parametrize('after', [1, 4, 6])
def test_store_failure_is_a_hard_hold_that_sends_nothing_more(after):
    """after=1: decision only; 4: entry filled, result not durable; 6: entry closed, the stop never decided."""
    w = World(flat_bars(20), sig(enter5='LONG', enter12='LONG'))
    w.run(4)
    w.journal.fail_writes(1, after=after)
    w.run(5)
    r = w.runner
    assert r.hard_hold is not None and r.hard_hold.startswith('recovery.durability_unavailable')
    n_events, n_orders = len(w.journal.read()), len(w.venue.orders_submitted())
    w.run(14)                                                      # the enter12 signal is NOT acted on
    assert len(w.journal.read()) == n_events and len(w.venue.orders_submitted()) == n_orders
    s = r.summary()
    assert s.mode == 'hold(durability_unavailable)' and s.counters['hard_holds'] == 1
    assert s.counters['hard_hold_cycles'] == 10
    if after >= 4:                                                 # filled but unprotected: visible, not raised
        assert s.counters['unprotected_cycles'] == 10
    w.restart()                                                    # leaving hard HOLD = a restart (fold + reconcile)
    w.runner.cycle(w.close_ms(14))
    assert w.runner.hard_hold is None
    stops = [o for o in w.venue.orders_submitted() if o.order_type == 'STOP_MARKET']
    assert len(stops) == (1 if after >= 4 else 0) and len(entries(w)) <= 1


# ------------------------------------------------------------------------------------------------ keyed opening risk
def _add_planned(r, key, lot):
    iid = ids.derive_intent_id(r.acct, key)
    return planned_intent(intent_id=iid, account_id=r.acct, decision_id=ids.derive_decision_id(r.acct, key),
                          purpose='add', symbol=SYM, side='LONG', qty=D('1'), reason=ReasonCode.ENTRY_PYRAMID,
                          at_ms=r.now, owner_id=lot.lot_id)


def _record_add(r, key, planned, *, decision_id=None):
    r._decision(decision_id=decision_id or ids.derive_decision_id(r.acct, key), action=Action.ADD,
                reason=ReasonCode.ENTRY_PYRAMID, authority=Authority.STRATEGY, key=key, symbol=SYM, side='LONG',
                intents=(planned,), subject_id=planned.owner_id)


def test_an_unkeyed_add_or_entry_is_refused_at_the_runner_boundary():
    w = World(flat_bars(20), sig(enter5='LONG'))
    w.run(6)
    r, lot = w.runner, w.runner.fold.open_lots()[0]
    key = ids.decision_key('injected', 'v1', '4h', SYM, 'LONG', w.close_ms(6), 'add')
    planned = _add_planned(r, key, lot)
    n = len(w.journal.read())
    with pytest.raises(UnkeyedOpeningDecision):                    # no key at all
        _record_add(r, None, planned, decision_id=ids.child_decision_id(planned.intent_id))
    with pytest.raises(UnkeyedOpeningDecision):                    # an entry key on an ADD
        _record_add(r, ids.decision_key('injected', 'v1', '4h', SYM, 'LONG', w.close_ms(6), 'entry'), planned)
    with pytest.raises(UnkeyedOpeningDecision):                    # an arbitrary decision id
        _record_add(r, key, planned, decision_id=ids.child_decision_id(planned.intent_id))
    with pytest.raises(UnkeyedOpeningDecision):
        r._decision(decision_id=ids.child_decision_id(planned.intent_id), action=Action.ENTER,
                    reason=ReasonCode.ENTRY_SIGNAL, authority=Authority.STRATEGY, key=None, symbol=SYM, side='LONG',
                    intents=())
    assert len(w.journal.read()) == n                              # nothing recorded


def test_a_keyed_add_is_consumed_once_also_across_a_restart():
    w = World(flat_bars(20), sig(enter5='LONG'))
    w.run(6)
    r, lot = w.runner, w.runner.fold.open_lots()[0]
    key = ids.decision_key('injected', 'v1', '4h', SYM, 'LONG', w.close_ms(6), Purpose.ADD)
    planned = _add_planned(r, key, lot)
    _record_add(r, key, planned)
    with pytest.raises(JournalConflict):                           # the same candle / signal again
        _record_add(r, key, planned)
    w.restart()
    r = w.runner
    r.now = w.close_ms(6)
    assert not claim_signal(w.journal.gate().grammar, key).fresh
    with pytest.raises(JournalConflict):                           # re-delivered after a restart
        _record_add(r, key, planned)
    assert sum(1 for d in r.fold.decisions.values() if d.action is Action.ADD) == 1


def test_duplicate_candle_and_redelivered_entry_signal_create_one_entry():
    dup = InjectedSignals({(SYM, T0 + 6 * H4): (('enter', 'LONG'), ('enter', 'LONG'))}, stop_atr=D('2'))
    w = World(flat_bars(20), dup)
    w.run(5)                                                       # the duplicate inside one candle
    w.runner.cycle(w.close_ms(5))                                  # the same candle delivered twice
    w.restart()
    w.runner.cycle(w.close_ms(5))                                  # and again after a restart
    w.run(8)
    assert len(entries(w)) == 1
    assert sum(1 for d in w.runner.fold.decisions.values() if d.action is Action.ENTER) == 1
    assert w.runner.counters.redelivered >= 1
