"""RT-02 regression tests for each guard line the mutation check found unpinned."""
from __future__ import annotations

import dataclasses
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import WALL0, StateSource, World, account_view, acct, pf  # noqa: E402
from test_rt02_journaling import A, KEY, Harness, _exit_plan, _plan_for  # noqa: E402
from test_rt02_projection import confirm  # noqa: E402

import newcore.account_context as nc04  # noqa: E402
from newcore.account_context import Operation  # noqa: E402
from newcore.domain import BindingChanged, IntentState, ReasonCode, make_id  # noqa: E402
from newcore.domain.account import BindingState  # noqa: E402
from newcore.management import ProtectionStatus  # noqa: E402
from newcore.ports import keys as K  # noqa: E402
from newcore.ports.venue import OrderOutcome, OutcomeKind  # noqa: E402
from newcore.runtime import JournalProjection  # noqa: E402

D = Decimal
LOT = K.derive_lot_id(A, K.derive_intent_id(A, KEY))


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    try:
        harness.close()
    except Exception:
        pass


def test_entry_is_refused_when_nc04_cannot_send_the_entry(h, monkeypatch):
    monkeypatch.delitem(nc04._ENDPOINTS, (Operation.SUBMIT_MARKET, 'classic'))
    assert h.enter()[0] is False and h.journal.last_sequence() == 0
    assert 'entry_route_unverified' in h.sink.snapshot().log


def test_entry_answered_known_is_unresolved_and_unprotected(h):
    h.venue.market = 'known'
    h.enter()
    assert h.drive() == ('order_unresolved', 'protection_failed')
    assert not any(c[0] == 'submit_stop' for c in h.venue.calls)


def _free_lot(h):
    _, decision = h.enter()
    h.drive()
    h.sink._lots[LOT].last_protect = ('algo', True, IntentState.CANCELLED)
    return decision


def test_close_larger_than_the_lot_is_refused(h):
    decision = _free_lot(h)
    plan = _exit_plan(LOT, decision.quantity * 2)
    seq = h.journal.last_sequence()
    assert h.sink.accept_management(A, plan) is False and h.journal.last_sequence() == seq


def test_management_for_a_closed_lot_is_refused(h):
    h.venue.stop = {'algo': 'rejected'}
    _, decision = h.enter()
    h.drive()                                                        # flattened: the lot is 0
    seq = h.journal.last_sequence()
    plan = _plan_for(LOT, decision.quantity, protection_status=ProtectionStatus.MISSING)
    assert h.sink.accept_management(A, plan) is False and h.journal.last_sequence() == seq


def _answer(h, kind, status, executed=None):
    def submit_market(order):
        h.venue.calls.append(('submit_market', order.ref.route, order.reduce, order.qty, None))
        return OrderOutcome(kind=kind, ref=order.ref, observed_at_ms=WALL0, status=status, exchange_order_id='5',
                            executed_qty=executed, avg_price=D('100') if executed else None)
    h.venue.submit_market = submit_market


def test_known_with_a_final_status_is_unresolved(h):
    _answer(h, OutcomeKind.KNOWN, 'FILLED')
    h.enter()
    assert 'order_unresolved' in h.drive()
    assert h.kinds()[-1] == ('state', 'unknown')


def test_final_with_an_open_status_is_unresolved(h):
    _answer(h, OutcomeKind.FINAL, 'NEW', executed=D('0'))
    h.enter()
    assert 'order_unresolved' in h.drive()
    assert h.kinds()[-1] == ('state', 'unknown') and h.sink.snapshot().lots == ()


# ------------------------------------------------------------------------------------------------ projection
def test_a_binding_that_left_confirmed_is_not_projected(h):
    rec = confirm(h)
    seq = h.journal.last_sequence() + 1
    h.journal.append(BindingChanged(
        event_id=make_id('evt', 10 ** 9 + seq), account_id=A, aggregate_id=pf(1), sequence=seq, at_ms=WALL0 + 1,
        reason=ReasonCode.BINDING_MISMATCH, from_state=BindingState.CONFIRMED, to_state=BindingState.MISMATCH,
        binding=rec.binding, confirmation=None, reconciliation_id=None))
    with pytest.raises(ValueError, match='binding_not_confirmed'):
        JournalProjection().load_journal(A, h.journal)


class _View:
    """A journal whose state is replaced, for book shapes the grammar cannot produce directly."""

    def __init__(self, real, **state):
        self.real, self.changes = real, state
        self.account_id = real.account_id

    def read(self):
        return self.real.read()

    def state(self):
        return dataclasses.replace(self.real.state(), **self.changes)


def test_a_negative_book_is_refused(h):
    confirm(h)
    with pytest.raises(ValueError, match='book_negative'):
        JournalProjection().load_journal(A, _View(h.journal, booked=((('BTCUSDT', 'LONG'), D('-1')),)))


def test_a_final_but_unclosed_intent_projects_its_executed_quantity(h):
    confirm(h)
    h.enter()
    h.drive()
    state = h.journal.state()
    (stop,) = state.intents
    final = types.SimpleNamespace(executed_qty=D('3'))
    view = dataclasses.replace(stop, final_result=final)
    durable = JournalProjection().load_journal(A, _View(h.journal, intents=(view,)))
    assert durable.orders[0].executed_qty == D('3')


# ------------------------------------------------------------------------------------------------ runtime
class JournalOnly(StateSource):
    def load(self, account_id, journal_state):
        raise AssertionError('the runtime must prefer load_journal')

    def load_journal(self, account_id, journal):
        return StateSource.load(self, account_id, journal.state())


def test_runtime_prefers_load_journal(tmp_path):
    w = World(tmp_path, 1)
    w.extra_ports[acct(1)] = {'state_source': JournalOnly(w.truths[acct(1)])}
    try:
        rt = w.boot()
        assert account_view(rt.snapshot(), acct(1)).restore_proven
    finally:
        w.shutdown()
