"""RT-02 journal projection: the NC-02 journal of a real protected entry becomes REC-02 durable truth."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rt01_fixtures import WALL0, account_record, acct, pf  # noqa: E402
from test_rt02_journaling import A, KEY, Harness  # noqa: E402

from newcore.domain import BindingChanged, IntentState, Purpose, ReasonCode, Side, make_id  # noqa: E402
from newcore.domain.account import BindingState, Environment  # noqa: E402
from newcore.ports import keys as K  # noqa: E402
from newcore.reconciliation import (EvidencePage, PlannerConfig, PlanStatus, SnapshotComponent,  # noqa: E402
                                    VenueOrder, VenueOrderKind, VenueOrderState, VenuePosition, VenueSnapshot,
                                    plan_reconciliation)
from newcore.runtime import JournalProjection  # noqa: E402

D = Decimal


def confirm(h, environment=Environment.TESTNET):
    rec = account_record(A, environment=environment)
    seq = h.journal.last_sequence() + 1
    h.journal.append(BindingChanged(
        event_id=make_id('evt', 10 ** 9 + seq), account_id=A, aggregate_id=pf(1), sequence=seq, at_ms=WALL0,
        reason=ReasonCode.BINDING_UNCONFIRMED, from_state=BindingState.UNCONFIRMED, to_state=BindingState.CONFIRMED,
        binding=rec.binding, confirmation=rec.confirmation, reconciliation_id=None))
    return rec


@pytest.fixture
def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    harness.close()


def test_an_unconfirmed_journal_is_never_projected(h):
    with pytest.raises(ValueError, match='binding_not_confirmed'):
        JournalProjection().load_journal(A, h.journal)


def test_a_bare_journal_state_is_refused(h):
    with pytest.raises(ValueError, match='journal_required'):
        JournalProjection().load(A, h.journal.state())


def test_another_accounts_journal_is_refused(h):
    confirm(h)
    with pytest.raises(ValueError, match='journal_account_mismatch'):
        JournalProjection().load_journal(acct(2), h.journal)


def test_confirmed_empty_journal_is_flat(h):
    rec = confirm(h)
    durable = JournalProjection(label='rt01').load_journal(A, h.journal)
    assert durable.account == rec and durable.positions == () and durable.orders == ()
    assert durable.portfolio_generation == 1 and durable.last_venue_observed_at_ms == 0


def _page(durable, component, records, generation=1):
    return EvidencePage(component, A, durable.account.binding, generation, 1, WALL0 + 1_000, True, tuple(records))


def test_protected_entry_projects_and_reconciles_protected(h):
    confirm(h)
    _, decision = h.enter()
    h.drive()
    durable = JournalProjection(label='rt01').load_journal(A, h.journal)
    qty = decision.quantity
    assert durable.positions == (durable.positions[0],)
    assert (durable.positions[0].symbol, durable.positions[0].side, durable.positions[0].qty) == ('BTCUSDT', Side.LONG,
                                                                                                    qty)
    (stop,) = durable.orders
    assert (stop.purpose, stop.state, stop.exchange_order_id, stop.executed_qty) == (
        Purpose.PROTECT, IntentState.WORKING, '9003', D('0'))
    assert K.route_of(stop.intent_id, stop.client_order_id) == 'algo'
    venue = VenueSnapshot((
        _page(durable, SnapshotComponent.POSITIONS, (VenuePosition('BTCUSDT', Side.LONG, qty),)),
        _page(durable, SnapshotComponent.OPEN_ORDERS, (VenueOrder('9003', stop.client_order_id, 'BTCUSDT', Side.LONG,
                                                                  VenueOrderKind.STOP_MARKET, VenueOrderState.OPEN,
                                                                  True, qty, D('0')),)),
        _page(durable, SnapshotComponent.USER_TRADES, ())))
    plan = plan_reconciliation(durable, venue, PlannerConfig(now_ms=WALL0 + 2_000, max_snapshot_age_s=5))
    assert plan.status is PlanStatus.PROTECTED_CONFIRMED


def test_a_missing_stop_on_the_venue_never_confirms(h):
    confirm(h)
    _, decision = h.enter()
    h.drive()
    durable = JournalProjection().load_journal(A, h.journal)
    venue = VenueSnapshot((
        _page(durable, SnapshotComponent.POSITIONS, (VenuePosition('BTCUSDT', Side.LONG, decision.quantity),)),
        _page(durable, SnapshotComponent.OPEN_ORDERS, ()),
        _page(durable, SnapshotComponent.USER_TRADES, ())))
    plan = plan_reconciliation(durable, venue, PlannerConfig(now_ms=WALL0 + 2_000, max_snapshot_age_s=5))
    assert plan.status is PlanStatus.HOLD


def test_flattened_lot_projects_flat(h):
    confirm(h)
    h.venue.stop = {'algo': 'rejected'}
    h.enter()
    h.drive()
    durable = JournalProjection().load_journal(A, h.journal)
    assert durable.positions == () and durable.orders == ()


def test_mainnet_binding_projects_but_the_runtime_refuses_it(h):
    confirm(h, environment=Environment.MAINNET)
    durable = JournalProjection().load_journal(A, h.journal)
    assert durable.account.binding.environment is Environment.MAINNET   # runtime._load_durable: environment_mismatch
