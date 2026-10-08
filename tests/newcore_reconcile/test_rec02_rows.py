"""REC-02 matrix rows (docs/newcore/REC02_TNET01_BUILD_PLAN.md section 3): one test (or more) per row id.

Every test asserts the gate sentence: the verdict ends FLAT, PROTECTED or an explicit HOLD with owner actions, or is
PENDING with the actions / reads that lead there (and PENDING turns into HOLD when the attempts run out).
"""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, HoldKind, IntentState, Ownership, ReasonCode
from newcore.reconcile import DecisionKind as K
from newcore.reconcile import Outcome, RecPolicy, reconcile, view_from_fold
from rec_helpers import (ENTRY_BAR, SYM, T, cid, fact, final, kinds, lot, ok, signals, snap, unknown, view, world_rec,
                         world_snapshot)
from slice_helpers import H4, World, flat_bars

STOP_BAR = ENTRY_BAR + 2                                # the bar whose low runs through the long stop (98.02)


def stop_market():
    return flat_bars(20, overrides={STOP_BAR: ('100', '100.5', '97', '97.5')})


def opened(side='LONG', bars=None):
    w = World(bars or flat_bars(20), signals(side))
    w.run(ENTRY_BAR)
    lot_, = w.runner.fold.open_lots()
    assert lot_.live_stop.state is IntentState.WORKING
    return w, lot_


def assert_terminal_or_pending(v):
    assert v.outcome in (Outcome.FLAT, Outcome.PROTECTED, Outcome.HOLD, Outcome.PENDING)
    if v.outcome is Outcome.HOLD:
        holds = v.of(K.HOLD) + v.of(K.QUARANTINE)
        assert holds and all(d.owner_actions for d in holds)
    if v.ownership is Ownership.KNOWN_EMPTY:
        assert v.outcome in (Outcome.FLAT, Outcome.HOLD) and not v.of(K.REREAD)


# ================================================================================================ group 1
def test_R09_vanished_stop_is_resolved_not_executed_and_restored_at_the_same_level():
    w, lot_ = opened()
    stop = lot_.live_stop
    w.venue.external_cancel(stop.intent.client_order_id)
    v, *_ = world_rec(w)
    assert kinds(v) == [('protect_only', 'R09', 'restore'), ('resolve_not_executed', 'R09', 'final_canceled')]
    p, = v.of(K.PROTECT_ONLY)
    assert (p.qty, p.price, p.lot_id) == (lot_.qty, stop.intent.stop_price, lot_.lot_id)
    assert v.outcome is Outcome.PENDING
    w.runner.cycle(w.venue.now_ms, decide=False)          # the runner restores it (its own journal-first path)
    v2, *_ = world_rec(w)
    assert v2.outcome is Outcome.PROTECTED and v2.decisions == ()
    assert_terminal_or_pending(v)


def test_R09_a_stop_unknown_to_the_venue_while_working_is_a_hold_never_assumed_gone():
    w, lot_ = opened()
    c = lot_.live_stop.intent.client_order_id
    w.venue.external_cancel(c)
    w.venue.not_found(c, times=5)
    v, *_ = world_rec(w)
    assert ('hold', 'R09', 'stop_not_found') in kinds(v)
    assert v.outcome is Outcome.HOLD and v.of(K.PROTECT_ONLY)          # protection is added, never removed
    assert_terminal_or_pending(v)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_R13_stop_filled_while_the_bot_was_offline_is_booked_from_exchange_evidence(side):
    bars = stop_market() if side == 'LONG' else flat_bars(20, overrides={STOP_BAR: ('100', '103', '99.5', '102.5')})
    w, lot_ = opened(side, bars)
    w.venue.advance_to(w.close_ms(STOP_BAR))            # the venue plays on; the runner is down
    w.restart()
    v, *_ = world_rec(w)
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.intent_id, r.qty) == ('R13', 'exchange_final', lot_.live_stop.intent_id, lot_.qty)
    assert any(e.startswith('trade:') for e in r.evidence)
    assert v.outcome is Outcome.PENDING and not v.of(K.HOLD)
    w.runner.cycle(w.venue.now_ms, decide=False)          # the runner journals the same FINAL by its own sync
    v2, *_ = world_rec(w)
    assert v2.outcome is Outcome.FLAT and v2.ownership is Ownership.KNOWN_EMPTY


def test_R13_triggered_algo_stop_is_booked_from_its_child_fills():
    stop = fact(2, 'protect', state=IntentState.WORKING, stop='90', owner='lot_' + '1' * 32, route='algo')
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop])
    child = '777'
    from rec_helpers import fill, known
    s = snap(positions=[], queries=[(stop.client_id, known(2, status='FINISHED', eoid=child, detail='algo_triggered',
                                                           route='algo'))],
             fills=[(child, ok([fill(child, '0.4', '90', trade='1'), fill(child, '0.6', '89', trade='2')]))])
    v = reconcile(vw, s, now_ms=T)
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.qty, r.price) == ('R13', 'algo_stop_filled', D('1'), D('89.4'))
    assert v.outcome is Outcome.PENDING


def test_R15_unreadable_reads_hold_and_never_claim_empty():
    v = reconcile(view(history=False), snap(pos_read=unknown(), ord_read=ok([])), now_ms=T)
    assert kinds(v) == [('hold', 'R15', 'unreadable')]
    assert v.outcome is Outcome.HOLD and v.ownership is Ownership.UNKNOWN
    assert v.hold_reasons == (ReasonCode.CONNECTIVITY_EXCHANGE_OUTAGE,)
    assert_terminal_or_pending(v)


def test_R15_unknown_entry_answer_resolved_by_a_final_record():
    e = fact(1, 'entry', state=IntentState.UNKNOWN)
    from rec_helpers import fill
    s = snap(positions=[__import__('rec_helpers').pos('1')], queries=[(e.client_id, final(1, '1', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1')]))])
    v = reconcile(view(intents=[e]), s, now_ms=T)
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.qty) == ('R08', 'late_fill', D('1'))
    # the position is now explained, but not protected yet: the runner's protect path follows (no level: HOLD)
    assert v.outcome is Outcome.HOLD and ('hold', 'R16', 'unprotected') in kinds(v)


def test_R15_unanswered_query_asks_for_a_reread_then_holds_after_the_attempts():
    e = fact(1, 'entry', state=IntentState.SUBMITTED)
    s = snap(positions=[])
    v0 = reconcile(view(intents=[e]), s, now_ms=T, attempt=0)
    assert v0.outcome is Outcome.PENDING and v0.needs.queries == (e.client_id,)
    v2 = reconcile(view(intents=[e]), s, now_ms=T, attempt=2)
    assert v2.outcome is Outcome.HOLD and ('hold', 'R15', 'unresolved_after_attempts') in kinds(v2)
    assert_terminal_or_pending(v2)


def test_R15_hold_clears_on_a_clean_fresh_match_after_a_lost_entry_answer():
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.venue.lose_next_market_answer('filled')
    w.run(ENTRY_BAR)
    f = w.runner.fold
    assert f.mode is EntriesMode.HOLD and set(f.mode_reasons) == {ReasonCode.EXEC_ENTRY_UNCONFIRMED}
    v, *_ = world_rec(w)
    assert kinds(v) == [('clear_hold', 'R15', 'fresh_full_match')]
    assert v.outcome is Outcome.PROTECTED and v.ownership is Ownership.KNOWN


def test_R15_hold_with_an_owner_reason_is_restated_never_cleared():
    vw = view(mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
              reasons=(ReasonCode.RECONCILE_UNRECONCILED, ReasonCode.OWNERSHIP_UNTRACKED_POSITION))
    v = reconcile(vw, snap(), now_ms=T)
    assert kinds(v) == [('hold', 'R15', 'owner_resume_required')] and v.outcome is Outcome.HOLD
    hk = view(mode=EntriesMode.HOLD, hold_kind=HoldKind.DURABILITY_UNAVAILABLE,
              reasons=(ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE,))
    v2 = reconcile(hk, snap(), now_ms=T)
    assert v2.of(K.HOLD)[0].owner_actions == ('restart_with_writable_store',)
    unbound = view(mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL, reasons=(ReasonCode.EXEC_ENTRY_UNCONFIRMED,),
                   binding=False)
    assert not reconcile(unbound, snap(), now_ms=T).of(K.CLEAR_HOLD)


def test_R16_restart_rebuilds_the_same_verdict_from_the_journal():
    w, lot_ = opened()
    before, *_ = world_rec(w)
    w.restart()
    after, *_ = world_rec(w)
    assert before == after
    assert after.outcome is Outcome.PROTECTED and after.ownership is Ownership.KNOWN and after.decisions == ()


def test_R16_restart_after_a_crash_before_the_stop_send_protects_and_holds_for_the_same_id_resend():
    from slice_helpers import Crash
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.port.crash(2, 'before')                           # effect 1 = the entry, effect 2 = the stop
    with pytest.raises(Crash):
        w.run(ENTRY_BAR)
    w.restart()
    v, *_ = world_rec(w)
    lot_, = w.runner.fold.open_lots()
    p, = v.of(K.PROTECT_ONLY)
    assert (p.row, p.qty, p.price) == ('R16', lot_.qty, lot_.protects[0].intent.stop_price)
    assert ('hold', 'R02', 'reduce_only_not_found') in kinds(v)
    assert v.outcome is Outcome.HOLD
    assert_terminal_or_pending(v)


def test_R19_first_run_flat_is_known_empty_only_from_fresh_ok_reads():
    w = World(flat_bars(20), signals())
    v, vw, s = world_rec(w)
    assert not vw.has_history and v.outcome is Outcome.FLAT and v.ownership is Ownership.KNOWN_EMPTY
    stale = reconcile(vw, s, now_ms=w.venue.now_ms + RecPolicy().freshness_ms + 1)
    assert stale.outcome is Outcome.PENDING and stale.ownership is Ownership.UNKNOWN


def test_R19_first_run_against_a_non_flat_account_is_quarantined_never_empty():
    w = World(flat_bars(20), signals())
    w.venue.inject_position(SYM, 'LONG', D('3'), D('100'))
    v, vw, _ = world_rec(w)
    assert not vw.has_history
    assert ('quarantine', 'R19', 'init_non_flat') in kinds(v) and ('hold', 'R19', 'unprotected') in kinds(v)
    assert v.outcome is Outcome.HOLD and v.ownership is Ownership.UNKNOWN and v.quarantined == ((SYM, 'LONG'),)
    assert_terminal_or_pending(v)


# ================================================================================================ group 2
def _lost_entry(truth):
    """The entry's answer is lost and its client id stays NOT_FOUND: the runner holds it UNKNOWN (never re-sent)."""
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.venue.lose_next_market_answer(truth)
    orig = w.venue.submit_market

    def submit(order):
        w.venue.not_found(order.ref.client_id, times=50)
        return orig(order)
    w.venue.submit_market = submit
    w.run(ENTRY_BAR)
    entry = next(iv for iv in w.runner.fold.intents.values() if iv.purpose.value == 'entry')
    assert entry.state is IntentState.UNKNOWN and w.runner.fold.mode is EntriesMode.HOLD
    return w, entry


def test_R01_lost_entry_inside_the_visibility_window_proves_nothing():
    w, entry = _lost_entry('not_filled')
    v, *_ = world_rec(w)
    assert ('hold', 'R01', 'within_visibility_window') in kinds(v) and v.outcome is Outcome.HOLD
    assert not v.of(K.RESOLVE_NOT_EXECUTED)


def test_R01_lost_entry_needs_two_agreeing_reads_then_resolves_not_executed():
    from newcore.domain import PositionRead
    w, entry = _lost_entry('not_filled')
    w.venue.advance_to(w.close_ms(ENTRY_BAR + 1))           # past the window; the runner did not cycle
    one, *_ = world_rec(w)
    assert ('hold', 'R01', 'awaiting_corroboration') in kinds(one) and not one.of(K.RESOLVE_NOT_EXECUTED)
    start = entry.sent_at_ms + RecPolicy().visibility_ms
    v, *_ = world_rec(w, corroboration={entry.intent_id: (PositionRead(at_ms=start, qty=D(0)),)})
    r, = v.of(K.RESOLVE_NOT_EXECUTED)
    assert (r.row, r.detail, r.intent_id) == ('R01', 'not_found_corroborated', entry.intent_id)
    assert v.outcome is Outcome.PENDING


def test_R01_lost_entry_that_did_fill_is_adopted_from_agreeing_reads_and_protected():
    from newcore.domain import PositionRead
    w, entry = _lost_entry('filled')
    w.venue.advance_to(w.close_ms(ENTRY_BAR + 1))
    qty = w.venue.positions(SYM).value[0].qty
    start = entry.sent_at_ms + RecPolicy().visibility_ms
    v, *_ = world_rec(w, corroboration={entry.intent_id: (PositionRead(at_ms=start, qty=qty),)},
                      stop_hints={(SYM, 'LONG'): D('98')})
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.qty) == ('R01', 'position_adopted', qty)
    p, = v.of(K.PROTECT_ONLY)
    assert (p.qty, p.price) == (qty, D('98'))


def test_R02_reduce_only_order_unknown_to_the_venue_holds_for_the_same_id_resend():
    close = fact(3, 'close', state=IntentState.SUBMITTED, owner='lot_' + f'{1:032x}')
    stop = fact(2, 'protect', stop='90', owner='lot_' + f'{1:032x}')
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[stop, close])
    from rec_helpers import not_found, order, pos
    v = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id)],
                           queries=[(close.client_id, not_found(3))]), now_ms=T)
    assert kinds(v) == [('hold', 'R02', 'reduce_only_not_found')]
    assert v.of(K.HOLD)[0].owner_actions == ('resend_same_client_id',) and v.outcome is Outcome.HOLD


def _manual_reduce(w, qty):
    """A manual close on the exchange (the owner, outside the bot): FakeVenue's own fill bookkeeping."""
    px = w.venue.bar(SYM, w.venue.now_ms).open
    w.venue._apply_fill(SYM, 'LONG', qty, px, reduce=True, eoid='880001', at_ms=w.venue.now_ms, fee=D(0))


def test_R03_manual_full_close_is_adopted_from_user_trades_and_the_left_stop_is_an_owner_item():
    w, lot_ = opened()
    w.run(ENTRY_BAR + 1)
    _manual_reduce(w, lot_.qty)
    v, *_ = world_rec(w)
    a, = v.of(K.ADOPT)
    assert (a.row, a.detail, a.qty) == ('R03', 'external_reduce', lot_.qty)
    assert any('order:880001' in e for e in a.evidence)
    h, = v.of(K.HOLD)
    assert h.detail == 'protection_left_on_flat_side' and h.owner_actions[0].startswith('cancel_order:')
    assert v.outcome is Outcome.HOLD


def test_R03_deficit_without_trades_asks_for_them_and_without_q2_is_an_owner_hold():
    w, lot_ = opened()
    w.run(ENTRY_BAR + 1)
    _manual_reduce(w, lot_.qty)
    v, *_ = world_rec(w, trades=False)
    assert v.outcome is Outcome.PENDING and v.needs.trades and not v.of(K.ADOPT)
    v2, *_ = world_rec(w, policy=RecPolicy(adopt_external_change=False))
    assert ('hold', 'R03', 'unexplained_deficit') in kinds(v2) and not v2.of(K.ADOPT)


def test_R04_foreign_order_is_quarantined_per_side_never_touched():
    from rec_helpers import order
    w, lot_ = opened()
    foreign = order('web_manual_1', reduce=False, type_='LIMIT', stop=None, eoid='990001')
    v, *_ = world_rec(w, orders_extra=(foreign,))
    q, = v.of(K.QUARANTINE)
    assert (q.row, q.detail, q.client_id) == ('R04', 'foreign_order', 'web_manual_1')
    assert v.quarantined == ((SYM, 'LONG'),) and v.outcome is Outcome.HOLD and not v.of(K.PROTECT_ONLY)
    whole, *_ = world_rec(w, orders_extra=(foreign,), policy=RecPolicy(quarantine_per_side=False))
    assert whole.quarantined == ((None, None),)


def test_R05_unjournaled_newcore_order_and_emergency_stop_are_owner_items():
    from newcore.ports.keys import client_id_for
    from rec_helpers import order
    w, lot_ = opened()
    ghost = order(client_id_for('int_' + 'f' * 32), reduce=False, type_='LIMIT', stop=None, eoid='990002')
    emergency = order('zbn1e-' + 'a' * 26, qty=str(lot_.qty), stop='97', eoid='990003')
    v, *_ = world_rec(w, orders_extra=(ghost, emergency))
    assert ('quarantine', 'R05', 'unjournaled_newcore') in kinds(v)
    assert ('hold', 'R05', 'emergency_stop') in kinds(v)
    assert not v.of(K.PROTECT_ONLY) and v.outcome is Outcome.HOLD


def test_R06_foreign_position_is_quarantined_and_flagged_unprotectable():
    w, lot_ = opened()
    w.venue.inject_position(SYM, 'SHORT', D('2'), D('100'))
    v, *_ = world_rec(w)
    assert ('quarantine', 'R06', 'foreign_position') in kinds(v) and ('hold', 'R06', 'unprotected') in kinds(v)
    assert v.quarantined == ((SYM, 'SHORT'),) and v.outcome is Outcome.HOLD
    assert not [d for d in v.of(K.PROTECT_ONLY) if d.side == 'SHORT']        # no owned stop level on that side


def test_R07_partial_fill_of_a_working_entry_holds_and_protects_the_filled_part():
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.venue.rest_next_entries(1)
    w.run(ENTRY_BAR)
    entry = next(iv for iv in w.runner.fold.intents.values() if iv.purpose.value == 'entry')
    assert entry.state is IntentState.WORKING
    w.venue.fill_resting(entry.intent.client_order_id, D('1'))
    v, *_ = world_rec(w, stop_hints={(SYM, 'LONG'): D('98')})
    assert ('hold', 'R07', 'partial_working') in kinds(v)
    p, = v.of(K.PROTECT_ONLY)
    assert (p.row, p.qty, p.price) == ('R07', D('1'), D('98'))
    bare, *_ = world_rec(w)
    assert ('hold', 'R07', 'unprotected') in kinds(bare) and not bare.of(K.PROTECT_ONLY)


def test_R07_partial_final_entry_books_only_the_executed_quantity():
    from rec_helpers import fill, pos
    e = fact(1, 'entry', qty='3', state=IntentState.SUBMITTED)
    s = snap(positions=[pos('1')], queries=[(e.client_id, final(1, '1', status='EXPIRED', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1')]))])
    v = reconcile(view(intents=[e], hints=[((SYM, 'LONG'), D('90'))]), s, now_ms=T)
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.qty) == ('R07', 'partial_final', D('1'))
    assert v.of(K.PROTECT_ONLY)[0].qty == D('1')


def test_R08_late_fill_supersedes_a_corroborated_not_found_under_q2():
    from newcore.domain.orders import Evidence
    from rec_helpers import fill, pos
    e = fact(1, 'entry', state=IntentState.CANCELLED, executed='0', evidence=Evidence.NOT_FOUND_CORROBORATED,
             final_at=T - 60_000)
    s = snap(positions=[pos('1')], queries=[(e.client_id, final(1, '1', eoid='6001'))],
             fills=[('6001', ok([fill('6001', '1')]))])
    vw = view(intents=[e], hints=[((SYM, 'LONG'), D('90'))])
    from newcore.reconcile import plan_reads
    assert plan_reads(vw, now_ms=T).queries == (e.client_id,)
    v = reconcile(vw, s, now_ms=T)
    r, = v.of(K.RESOLVE_FILLED)
    assert (r.row, r.detail, r.qty) == ('R08', 'supersedes_not_found_corroborated', D('1'))
    assert not v.of(K.QUARANTINE)                                  # explained, not foreign
    off = reconcile(vw, s, now_ms=T, policy=RecPolicy(adopt_external_change=False))
    assert ('hold', 'R08', 'late_fill_after_corroboration') in kinds(off) and not off.of(K.RESOLVE_FILLED)


def test_R10_orphan_and_duplicate_owned_orders_are_owner_items_never_cancelled_here():
    from dataclasses import replace
    from rec_helpers import order, pos
    e = fact(1, 'entry', state=IntentState.FILLED, executed='1')
    stop = fact(2, 'protect', stop='90', owner='lot_' + f'{1:032x}')
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[e, stop])
    listed = [order(stop.client_id), order(e.client_id, reduce=False, type_='MARKET', stop=None, eoid='6001')]
    v = reconcile(vw, snap(positions=[pos('1')], orders=listed), now_ms=T)
    assert kinds(v) == [('hold', 'R10', 'orphan_owned')] and v.outcome is Outcome.HOLD
    dup = replace(fact(3, 'close', state=IntentState.FILLED, executed='0'), client_id=stop.client_id)
    v2 = reconcile(view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[e, stop, dup]),
                   snap(positions=[pos('1')], orders=[order(stop.client_id)]), now_ms=T)
    assert ('hold', 'R10', 'duplicate_client_id') in kinds(v2)
    gone = reconcile(view(intents=[e, stop]), snap(orders=[order(stop.client_id)]), now_ms=T)
    assert ('hold', 'R10', 'protect_without_lot') in kinds(gone) and gone.outcome is not Outcome.FLAT


def test_R11_manual_partial_close_is_adopted_and_the_rest_stays_protected():
    w, lot_ = opened()
    w.run(ENTRY_BAR + 1)
    _manual_reduce(w, D('1'))
    v, *_ = world_rec(w)
    a, = v.of(K.ADOPT)
    assert (a.row, a.detail, a.qty) == ('R11', 'external_reduce', D('1'))
    assert not v.of(K.HOLD) and not v.of(K.PROTECT_ONLY)            # the old stop still covers (over-covers)
    assert v.outcome is Outcome.PENDING


def test_R11_external_fills_that_do_not_add_up_to_the_deficit_are_never_adopted():
    from newcore.reconcile import TradeWindow
    from rec_helpers import fill, order, pos
    stop = fact(2, 'protect', qty='2', stop='90', owner='lot_' + f'{1:032x}')
    vw = view(lots=[lot(1, qty='2', stop_intent=stop.intent_id)], intents=[stop])
    tw = TradeWindow(from_ms=T - 10, read=ok([fill('880001', '0.5', trade='m1')]))
    v = reconcile(vw, snap(positions=[pos('1')], orders=[order(stop.client_id, qty='2')],
                           trades=[((SYM, 'LONG'), tw)]), now_ms=T)
    assert ('hold', 'R11', 'unexplained_deficit') in kinds(v) and not v.of(K.ADOPT)


def test_R12_manual_add_is_quarantined_and_its_surplus_protected_at_the_owned_level_q3():
    w, lot_ = opened()
    w.venue.inject_position(SYM, 'LONG', D('1'), D('100'))
    v, *_ = world_rec(w)
    q, = v.of(K.QUARANTINE)
    assert (q.row, q.detail, q.qty) == ('R12', 'manual_add', D('1'))
    p, = v.of(K.PROTECT_ONLY)
    assert (p.row, p.qty, p.price) == ('R12', D('1'), lot_.protects[0].intent.stop_price)
    off, *_ = world_rec(w, policy=RecPolicy(protect_surplus=False))
    assert not off.of(K.PROTECT_ONLY) and ('hold', 'R12', 'unprotected') in kinds(off)


def test_R14_stale_read_rereads_then_holds_and_never_clears():
    from dataclasses import replace
    from newcore.ports.venue import ReadOutcome
    w = World(flat_bars(20), signals())
    w.run(ENTRY_BAR - 1)
    w.venue.lose_next_market_answer('filled')
    w.run(ENTRY_BAR)                                     # HOLD (auto-clearable reason), position protected

    def lag(s):
        p = s.positions
        return replace(s, positions=ReadOutcome(kind=p.kind, observed_at_ms=p.observed_at_ms - 1, value=p.value))
    v, *_ = world_rec(w, transform=lag)
    assert kinds(v) == [('reread', 'R14', 'stale_read')] and v.outcome is Outcome.PENDING
    v2, *_ = world_rec(w, transform=lag, attempt=2)
    assert kinds(v2) == [('hold', 'R14', 'stale_read')] and v2.outcome is Outcome.HOLD
    fresh, *_ = world_rec(w)
    assert fresh.of(K.CLEAR_HOLD)                        # the same account with a fresh read does clear


def test_R14_a_diff_right_after_a_recorded_fill_settles_before_it_is_judged():
    from rec_helpers import order, pos
    e = fact(1, 'entry', state=IntentState.FILLED, executed='1', final_at=T - 1000)
    stop = fact(2, 'protect', stop='90', owner='lot_' + f'{1:032x}')
    vw = view(lots=[lot(1, stop_intent=stop.intent_id)], intents=[e, stop], newest=T - 1000)
    s = snap(positions=[], orders=[order(stop.client_id)])          # positionRisk has not caught up with the fill
    pol = RecPolicy(settle_ms=5000)
    v = reconcile(vw, s, now_ms=T, policy=pol)
    assert kinds(v) == [('reread', 'R14', 'settling')] and v.outcome is Outcome.PENDING
    last = reconcile(vw, s, now_ms=T, policy=pol, attempt=pol.max_attempts - 1)
    assert ('reread', 'R14', 'settling') not in kinds(last) and last.outcome is Outcome.HOLD
    later = snap(positions=[], orders=[order(stop.client_id)], at=T + 10_000)
    old = reconcile(vw, later, now_ms=T + 10_000, policy=pol)      # a read long after the fill: judged at once
    assert ('reread', 'R14', 'settling') not in kinds(old)
    assert ('reread', 'R14', 'settling') not in kinds(reconcile(vw, s, now_ms=T))     # settle_ms 0 = off


def test_R17_fills_that_disagree_with_the_final_record_hold_and_book_nothing():
    from dataclasses import replace
    from rec_helpers import fill
    w, lot_ = opened(bars=stop_market())
    w.venue.advance_to(w.close_ms(STOP_BAR))
    w.restart()

    def skew(s):
        return replace(s, fills=tuple((e, ok([fill(e, '0.001', '98', trade='t')], r.observed_at_ms))
                                      for e, r in s.fills))
    v, *_ = world_rec(w, transform=skew)
    assert ('hold', 'R17', 'fill_mismatch') in kinds(v) and not v.of(K.RESOLVE_FILLED)
    assert v.outcome is Outcome.HOLD


def test_R18_stop_that_differs_from_the_journal_is_not_counted_and_is_an_owner_item():
    from dataclasses import replace
    from newcore.ports.venue import ReadOutcome
    w, lot_ = opened()

    def loosen(s):
        o = s.orders
        moved = tuple(replace(x, stop_price=x.stop_price - 5) for x in o.value)
        return replace(s, orders=ReadOutcome(kind=o.kind, observed_at_ms=o.observed_at_ms, value=moved))
    v, *_ = world_rec(w, transform=loosen)
    assert ('hold', 'R18', 'order_mismatch') in kinds(v)
    p, = v.of(K.PROTECT_ONLY)                            # not counted as cover: protection at the journaled level
    assert (p.qty, p.price) == (lot_.qty, lot_.protects[0].intent.stop_price)
