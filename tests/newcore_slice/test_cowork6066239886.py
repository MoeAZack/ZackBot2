"""Cowork's run on 3b65f24 (PR #37, issuecomment 6066239886) - repros first:

1   hard-HOLD sizing, the NEXT cycles: ours 5 + foreign 2, the stop refused -> the emergency close is sized 5, but the
    store stays down, the closed quantity stays in fold.open_lots() and the foreign 2 was then treated as ours and
    closed too. Now our emergency fills (venue trades of our zbn1e orders, matched by order id; this process's own
    FINAL results as a fallback) are subtracted from the owned quantity: the foreign qty is never touched, over any
    number of hard-HOLD cycles, across a restart still in hard HOLD, and at the store-back handover (journaled there as
    an owner item; the lot keeps no protection beyond what is still ours).
2   adv6 (EXC class): a stop / close SEND that raises (the adapter breaks its typed-outcome contract) aborted the cycle
    before any HOLD -> naked and ACTIVE. A raising venue call is now an UNKNOWN answer (a read: UNKNOWN) with an
    incident: the unknown-stop escalation and the durable HOLD run as for a lost answer - never ACTIVE while naked.
3   the two PROTECT cancels in normal HOLD (flat side / confirmed smaller replacement) go through ONE named check
    (Runner.protect_cancel_in_hold), the single place the central cell will be wired into."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode
from newcore.ports.venue import OrderOutcome, OutcomeKind
from newcore.runner import InjectedSignals, ids
from slice_helpers import H4, ScriptedVenue, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side
            and o.order_type == 'STOP_MARKET']


def closes(w, side):
    return [o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and o.reduce and o.position_side == side]


def hard_hold_with_foreign(side, code=-2010):
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.inject_position(SYM, side, D('2'), D('100'))                 # a foreign add on the same side
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)       # our stop is gone ...
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')                           # ... the store is down
    w.port.refuse('stop', code)                                          # ... and no stop can be placed
    return w


# ------------------------------------------------------------------------------------------------------- 1
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('code', (-2010, -2021))
def test_1_the_foreign_quantity_is_never_closed_over_many_hard_hold_cycles(side, code):
    w = hard_hold_with_foreign(side, code)
    for b in range(7, 12):
        w.run(b)
        assert position(w, side) == D('2')                               # ours closed, the foreign 2 untouched
    assert [o.qty for o in closes(w, side)] == [D('5')]                  # one emergency close, ours only


@pytest.mark.parametrize('side', SIDES)
def test_1_a_restart_still_in_hard_hold_re_derives_our_emergency_fills_from_the_venue(side):
    w = hard_hold_with_foreign(side)
    w.run(7)
    assert position(w, side) == D('2')
    w.restart(hard_hold='test: store still down at boot')                # a new process: no in-memory ledger
    w.run(10)
    assert position(w, side) == D('2') and [o.qty for o in closes(w, side)] == [D('5')]


@pytest.mark.parametrize('side', SIDES)
def test_1_store_back_journals_the_emergency_fill_and_never_protects_the_foreign_quantity(side):
    w = hard_hold_with_foreign(side)
    w.run(7)
    w.port._refuse.pop('stop')
    w.restart()                                                          # the store is writable again
    w.run(10)
    r = w.runner
    lot, = r.fold.open_lots()                                            # (the journal cannot book a zbn1e close)
    assert r.journal.find_decision(ids.marker_decision_id('emergency_fill', lot.lot_id)) is not None
    assert any('emergency' in t and 'filled' in t for _, t in r.incidents)
    assert r.fold.mode is EntriesMode.HOLD and position(w, side) == D('2')
    assert stops(w, side) == [] and [o.qty for o in closes(w, side)] == [D('5')]   # nothing for the foreign 2


# ------------------------------------------------------------------------------------------------------- 2
class Raising(ScriptedVenue):
    """adv6 EXC: the adapter raises instead of answering (stop / reduce sends)."""
    raising = frozenset()

    def _effect(self, name, call, ref, kind):
        if kind in self.raising:
            self.effects.append(name)
            raise ConnectionError('venue adapter raised')
        return super()._effect(name, call, ref, kind)


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('stop_how,close_how', [('exc', 'exc'), ('exc', 'silent'), ('exc', -2010), ('exc', -2022),
                                                (-2010, 'exc'), (-2022, 'exc')])
def test_2_a_raising_stop_or_close_never_leaves_the_account_active_and_naked(side, stop_how, close_how):
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.port = Raising(w.venue)
    w.runner = w.new_runner()
    w.port.raising = frozenset(k for k, h in (('stop', stop_how), ('reduce', close_how)) if h == 'exc')
    for k, h in (('stop', stop_how), ('reduce', close_how)):
        if isinstance(h, int):
            w.port.refuse(k, h)
    w.run(11)                                                            # no exception escapes a cycle
    r = w.runner
    pos = position(w, side)
    naked = pos > sum((o.qty for o in stops(w, side)), D(0))
    assert not (naked and r.fold.mode is EntriesMode.ACTIVE)
    if naked:
        assert r.fold.mode is EntriesMode.HOLD and any(t.startswith('I1') for _, t in r.incidents)
        assert any('raised' in t for _, t in r.incidents)
        entries = [o for o in w.venue.orders_submitted() if not o.reduce]
        assert len(entries) == 1                                         # no new entries


# ------------------------------------------------------------------------------------------------------- 3
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('case', ('flat_side', 'replaced'))
def test_3_both_hold_protect_cancels_go_through_the_one_named_check(side, case, monkeypatch):
    from newcore.runner.runner import Runner
    asked = []

    def deny(self, kind, stop):
        asked.append(kind)
        return False
    monkeypatch.setattr(Runner, 'protect_cancel_in_hold', deny)
    w = World(flat_bars(20), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue._apply_fill(SYM, side, lot.qty if case == 'flat_side' else D('2'), D('100'), reduce=True,
                        eoid='manual-close', at_ms=w.venue.now_ms, fee=D('0'))
    if case == 'replaced':
        # Cowork 6073089838: a reduce-only close that is not ours is ownership UNKNOWN (never resized down). To still
        # drive the resize-down gate, a test instrument makes the lookup PROVE the order foreign (an opening record).
        import dataclasses
        inner = w.venue.order_by_id

        def order_by_id(symbol, eoid):
            r = inner(symbol, eoid)
            if eoid != 'manual-close':
                return r
            return dataclasses.replace(r, value=(dataclasses.replace(
                r.value[0], side='BUY' if side == 'LONG' else 'SELL', reduce_only=False),))
        w.port.order_by_id = order_by_id
    w.run(8)
    assert asked and set(asked) == {case}
    assert lot.live_stop.intent.client_order_id in {o.ref.client_id for o in stops(w, side)}   # denied: kept
