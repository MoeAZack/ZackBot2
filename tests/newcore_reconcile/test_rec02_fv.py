"""REC-02 rows driven end to end on FakeVenue through FaultVenue (newcore/reconcile/testing.py): the runner trades
through FaultVenue, the venue changes or lies, and the fold reconciles the runner's REAL journal fold against the same
venue. These re-express the rows the first pass proved on built inputs (R02, R07, R08, R10, R11, R14, R17, R18) and
move R03 / R04 / R05 onto venue hooks."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, IntentState, Ownership, Purpose
from newcore.domain.orders import Evidence
from newcore.ports.keys import client_id_for
from newcore.ports.venue import OrderRef
from newcore.reconcile import DecisionKind as K
from newcore.reconcile import Outcome
from rec_helpers import ENTRY_BAR, SYM, fault_world, kinds, signals, world_rec
from slice_helpers import Crash, flat_bars

EXIT_BAR = 10
STOP_BAR = ENTRY_BAR + 2


def opened(*, exit_=None, bars=None, side='LONG'):
    w = fault_world(bars or flat_bars(20), signals(side, exit_=exit_))
    w.run(ENTRY_BAR)
    lot, = w.runner.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING
    return w, lot


def on_entry_submit(w, fn):
    """Run fn(client_id) just before the entry reaches the venue (the id is derived inside the runner)."""
    orig = w.fault.submit_market

    def submit(order):
        if not order.reduce:
            fn(order.ref.client_id)
        return orig(order)
    w.fault.submit_market = submit


def entry_of(w):
    return next(iv for iv in w.runner.fold.intents.values() if iv.purpose is Purpose.ENTRY)


# ------------------------------------------------------------------------------------------------ R02
def test_R02_fv_close_lost_before_it_reached_the_venue_holds_for_the_resend_and_protects():
    w, lot = opened(exit_=EXIT_BAR)
    w.run(EXIT_BAR - 1)
    # Codex ruling 13 (TNET N6): the close goes out BEFORE the stop is cancelled, so the effects are entry, stop,
    # CLOSE <- dies before it, (stop cancel). The stop is still live: the fold holds for the lost close only and has
    # nothing to re-protect (the pre-N6 order died with the stop already cancelled: PROTECT_ONLY then).
    w.port.crash(3, 'before')
    with pytest.raises(Crash):
        w.run(EXIT_BAR)
    w.restart()
    v, *_ = world_rec(w)
    assert ('hold', 'R02', 'reduce_only_not_found') in kinds(v)
    assert not v.of(K.PROTECT_ONLY) and lot.protects[0].intent.client_order_id in {
        o.ref.client_id for o in w.venue.open_orders().value}
    assert v.outcome is Outcome.HOLD
    w.runner.cycle(w.venue.now_ms, decide=False)   # the runner re-sends the close under the SAME client id
    v2, *_ = world_rec(w)
    assert v2.outcome is Outcome.FLAT and v2.decisions == () and v2.ownership is Ownership.KNOWN_EMPTY
    assert all(p.qty == 0 for p in w.venue.positions().value)


# ------------------------------------------------------------------------------------------------ R07
def test_R07_fv_lost_answer_of_a_partially_executed_entry_books_only_the_executed_part():
    w = fault_world(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.fault.partial_next_entry(D('1'))
    w.venue.lose_next_market_answer('filled')
    on_entry_submit(w, w.port.blind)               # the runner cannot read it; the reconciliation can
    w.run(ENTRY_BAR)
    e = entry_of(w)
    assert e.state is IntentState.UNKNOWN and w.runner.fold.open_lots() == []
    v, *_ = world_rec(w, stop_hints={(SYM, 'LONG'): D('98')})
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.intent_id, r.qty) == ('R07', 'partial_final', e.intent_id, D('1'))
    assert 'status:EXPIRED' in r.evidence and any(x.startswith('trade:') for x in r.evidence)
    p, = v.of(K.PROTECT_ONLY)
    assert (p.qty, p.price) == (D('1'), D('98'))


def test_R07_fv_the_runner_itself_books_a_partial_final_entry_and_the_fold_agrees():
    w = fault_world(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.fault.partial_next_entry(D('1'))
    w.run(ENTRY_BAR)
    lot, = w.runner.fold.open_lots()
    assert lot.qty == D('1') and lot.live_stop.intent.qty == D('1')
    v, *_ = world_rec(w)
    assert v.outcome is Outcome.PROTECTED and v.decisions == ()


# ------------------------------------------------------------------------------------------------ R08
def test_R08_fv_late_final_supersedes_a_not_found_the_runner_corroborated_on_lagging_reads():
    w = fault_world(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    until = w.close_ms(ENTRY_BAR + 3)
    w.venue.lose_next_market_answer('filled')

    def blind(c):
        w.fault.not_found_until(c, until)
        w.fault.hide_position_until(SYM, 'LONG', until)
    on_entry_submit(w, blind)
    w.run(ENTRY_BAR + 2)                           # two agreeing (lagging) flat reads after the window
    e = entry_of(w)
    assert e.state is IntentState.CANCELLED and e.final.evidence is Evidence.NOT_FOUND_CORROBORATED
    w.venue.advance_to(until)                      # the venue catches up; the runner has not cycled yet
    v, *_ = world_rec(w, stop_hints={(SYM, 'LONG'): D('98')})
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.intent_id) == ('R08', 'supersedes_not_found_corroborated', e.intent_id)
    assert r.qty == w.venue.positions(SYM).value[0].qty
    assert not v.of(K.QUARANTINE)                  # explained by the owned client id, not a foreign position
    assert v.of(K.PROTECT_ONLY)


# ------------------------------------------------------------------------------------------------ R10
def test_R10_fv_a_wrong_cancel_ack_leaves_an_orphan_stop_the_owner_must_cancel():
    w, lot = opened(exit_=EXIT_BAR)
    stop_cid = lot.live_stop.intent.client_order_id
    w.fault.fake_cancel(stop_cid)
    w.run(EXIT_BAR)                                # signal close: stop "cancelled", position closed
    assert w.runner.fold.open_lots() == []
    assert all(p.qty == 0 for p in w.venue.positions().value) and w.venue.open_orders().value
    v, *_ = world_rec(w)
    h = [d for d in v.of(K.HOLD) if d.row == 'R10']
    assert [d.detail for d in h] == ['orphan_owned']
    assert h[0].owner_actions == (f'cancel_orphan:{stop_cid}',)
    assert v.outcome is Outcome.HOLD and v.ownership is not Ownership.KNOWN_EMPTY


# ------------------------------------------------------------------------------------------------ R03 / R11
def test_R03_fv_manual_full_close_is_explained_from_user_trades_never_adopted():
    w, lot = opened()
    w.run(ENTRY_BAR + 1)
    eoid = w.fault.manual_close(SYM, 'LONG', lot.qty)
    v, *_ = world_rec(w)
    assert not v.of(K.ADOPT)
    h, = v.of(K.HOLD)
    assert (h.row, h.detail, h.qty) == ('R03', 'external_close_explained', lot.qty)
    assert any(f'order:{eoid}' in x for x in h.evidence)


def test_R11_fv_manual_partial_close_is_adopted_and_the_rest_stays_protected():
    w, lot = opened()
    w.run(ENTRY_BAR + 1)
    w.fault.manual_close(SYM, 'LONG', D('1'))
    v, *_ = world_rec(w)
    a, = v.of(K.ADOPT)
    assert (a.row, a.detail, a.qty) == ('R11', 'external_reduce', D('1'))
    assert not v.of(K.HOLD) and not v.of(K.PROTECT_ONLY) and v.outcome is Outcome.PENDING


def test_R11_fv_manual_add_then_partial_close_is_never_adopted_as_a_reduction():
    w, lot = opened()
    w.run(ENTRY_BAR + 1)
    w.fault.manual_add(SYM, 'LONG', D('2'))
    w.fault.manual_close(SYM, 'LONG', D('1'))      # net +1: a surplus, not a deficit
    v, *_ = world_rec(w)
    assert not v.of(K.ADOPT) and ('quarantine', 'R12', 'manual_add') in kinds(v)


# ------------------------------------------------------------------------------------------------ R04 / R05
def test_R04_fv_foreign_order_listed_by_the_venue_is_quarantined():
    w, lot = opened()
    w.fault.foreign_order('web_manual_1', symbol=SYM, side='LONG', qty='1')
    v, *_ = world_rec(w)
    q, = v.of(K.QUARANTINE)
    assert (q.row, q.detail) == ('R04', 'foreign_order') and v.quarantined == ((SYM, 'LONG'),)


def test_R05_fv_unjournaled_newcore_order_is_quarantined():
    w, lot = opened()
    w.fault.foreign_order(client_id_for('int_' + 'e' * 32), symbol=SYM, side='SHORT', qty='1')
    v, *_ = world_rec(w)
    q, = v.of(K.QUARANTINE)
    assert (q.row, q.detail, q.side) == ('R05', 'unjournaled_newcore', 'SHORT')


# ------------------------------------------------------------------------------------------------ R14
def test_R14_fv_stale_venue_reads_reread_then_hold_and_never_clear():
    w = fault_world(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.venue.lose_next_market_answer('filled')
    w.run(ENTRY_BAR)
    assert w.runner.fold.mode is EntriesMode.HOLD
    w.fault.stale_reads(2, 60_000)
    v, *_ = world_rec(w)
    assert kinds(v) == [('reread', 'R14', 'stale_read')] and not v.of(K.CLEAR_HOLD)
    w.fault.stale_reads(2, 60_000)
    v2, *_ = world_rec(w, attempt=2)
    assert kinds(v2) == [('hold', 'R14', 'stale_read')] and v2.ownership is not Ownership.KNOWN_EMPTY
    v3, *_ = world_rec(w)                          # the lag is gone: the same account clears
    assert v3.of(K.CLEAR_HOLD)


# ------------------------------------------------------------------------------------------------ R17
def test_R17_fv_fills_disagreeing_with_the_final_record_hold_and_book_nothing():
    bars = flat_bars(20, overrides={STOP_BAR: ('100', '100.5', '97', '97.5')})
    w, lot = opened(bars=bars)
    w.venue.advance_to(w.close_ms(STOP_BAR))       # the stop fills while the runner is down
    w.restart()
    stop_cid = lot.live_stop.intent.client_order_id
    eoid = w.venue.query(OrderRef(symbol=SYM, client_id=stop_cid)).exchange_order_id
    w.fault.skew_fills(eoid, qty=D('0.001'))
    v, *_ = world_rec(w)
    assert ('hold', 'R17', 'fill_mismatch') in kinds(v) and not v.of(K.RESOLVE_FILLED)
    w.fault._skew.clear()
    ok_, *_ = world_rec(w)
    assert ok_.of(K.RESOLVE_FILLED)                # the same stop with matching fills is booked


# ------------------------------------------------------------------------------------------------ R18
def test_R18_fv_stop_edited_on_the_exchange_is_not_counted_and_is_restated():
    w, lot = opened()
    stop = lot.live_stop.intent
    w.fault.move_stop(stop.client_order_id, stop.stop_price - 5)
    v, *_ = world_rec(w)
    assert ('hold', 'R18', 'order_mismatch') in kinds(v)
    p, = v.of(K.PROTECT_ONLY)
    assert (p.qty, p.price) == (lot.qty, stop.stop_price)
