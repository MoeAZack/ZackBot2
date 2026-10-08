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
