"""Codex #13 6070594544 (P1 at 859af74): _lots_since queried fills(order) for EVERY open lot, so an ordinary lot whose
redundant fills read failed was left naked in a hard HOLD. An ordinary entry's intent creation is a safe lower bound
(the complete trades read proves its quantity); only a RECOVERED entry (recorded after it filled) is widened to its
proven fills, and its unproven fills fail closed (UNKNOWN, loud; a durable incident once the store can write)."""
from decimal import Decimal as D

import pytest

from evidence_faults import FAULTS, faulty
from newcore.domain import EntriesMode, IntentState, ReasonCode
from newcore.runner import InjectedSignals
from slice_helpers import World, flat_bars
from test_cowork_new_a import SYM, at, lost_after_entry, position

pytestmark = pytest.mark.usefixtures('journal_kind')
SIDES = ('LONG', 'SHORT')


def new_orders(w, n):
    return list(w.venue.orders_submitted()[n:])


def hard_hold(w, restart):
    w.journal.fail_writes(10 ** 9)
    if restart:
        w.restart(hard_hold='test: store down at boot')
    else:
        w.runner.store_unavailable('test: ENOSPC')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', FAULTS)
@pytest.mark.parametrize('restart', (False, True))
def test_an_ordinary_lot_is_protected_whatever_its_fills_read_answers(side, fault, restart):
    w = World(flat_bars(30), InjectedSignals({(SYM, at(5)): (('enter', side),)}, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    hard_hold(w, restart)
    w.venue.fills = faulty(w.venue.fills, fault)                         # only the redundant fills read is bent
    n = len(w.venue.orders_submitted())
    for b in (7, 8):
        w.run(b)
    sent = new_orders(w, n)
    assert [o.qty for o in sent if o.order_type == 'STOP_MARKET'] == [lot.qty], [(o.order_type, o.qty) for o in sent]
    assert w.runner.mode is EntriesMode.HOLD


def recovered(side):
    """A lost-tail entry (filled at bar 6, every record lost) recovered and protected at bar 9: its intent is
    recorded AFTER its fill."""
    w = lost_after_entry(side)
    w.venue.advance_to(w.close_ms(8))
    w.runner = w.new_runner()
    w.run(10, from_bar=9)
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert w.runner._recovered_entry(lot.entry)
    fills = w.venue.fills(SYM, lot.entry.final.exchange_order_id).value
    assert fills and min(f.at_ms for f in fills) < lot.entry.intent.created_at_ms   # the fill predates the record
    return w, lot


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('restart', (False, True))
def test_a_recovered_entry_widens_to_its_proven_fill_and_is_protected(side, restart):
    w, lot = recovered(side)
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    hard_hold(w, restart)
    n = len(w.venue.orders_submitted())
    for b in (11, 12):
        w.run(b)
    assert [o.qty for o in new_orders(w, n) if o.order_type == 'STOP_MARKET'] == [lot.qty]


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('fault', ('raise', 'unknown', 'empty', 'trunc', 'stale', 'mismatch'))
def test_a_recovered_entry_with_unproven_fills_fails_closed(side, fault):
    w, lot = recovered(side)
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    hard_hold(w, False)
    w.venue.fills = faulty(w.venue.fills, fault)
    n = len(w.venue.orders_submitted())
    for b in (11, 12):
        w.run(b)
    r = w.runner
    assert new_orders(w, n) == [] and position(w, side) == lot.qty      # nothing sized from an unknown window
    assert r.mode is EntriesMode.HOLD
    assert any('recovered entry' in t and 'not proven' in t for _, t in r.incidents)
    assert any('UNREADABLE' in t for _, t in r.incidents)
    w.restart()                                                          # the store can write again
    w.run(13)
    incs = [e.incident for e in w.journal.read() if type(e).__name__ == 'IncidentRecorded']
    assert any(i.kind is ReasonCode.RECONCILE_UNRECONCILED and lot.lot_id in i.lot_refs
               and 'recovered entry' in i.detail for i in incs)
