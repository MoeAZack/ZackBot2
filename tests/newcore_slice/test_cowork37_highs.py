"""Cowork attacks routed late from PR #37 (cw37), the HIGHs - each a failing repro first, then fixed:

NEW-1  a crash after the RECONCILE decision of a lost entry is durable but before its result: the next corroboration
       re-emitted the same decision id -> JournalConflict 'G4 decision recorded twice' every second cycle, forever;
       a filled entry stayed naked.
F1     hard HOLD: the emergency stop id was H(account, symbol, side, qty) with no generation, and "found by id" counted
       as cover, so a second gap of the same size was never covered (pos 11 covered 8).
FUZZ   (20k, 289 'unprotected-in-HOLD, no stop'): the entry reached the venue and filled, then the last journal events
       were lost (the send record, the result); the restart closed the DURABLE entry as NOT_SENT (or, with the intent
       lost too, never looked) while the venue held its position: unowned and naked in HOLD for ever."""
from decimal import Decimal as D

import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import Action, DecisionRecorded, EntriesMode, IntentState, Purpose
from newcore.runner import InjectedSignals
from newcore.runner import ids
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, Crash, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def sig(side, bar=5):
    return InjectedSignals({(SYM, T0 + (bar + 1) * H4): (('enter', side),)}, stop_atr=D('2'))


def position(w, side):
    return next(p.qty for p in w.venue.positions().value if p.side == side)


def covered(w, side):
    return sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side), D(0))


def protected_end(w, side):
    return position(w, side) == 0 or covered(w, side) >= position(w, side)


# ------------------------------------------------------------------------------------------------------- NEW-1
def _entry_cid(w, side):
    key = ids.decision_key('injected', 'v1', '4h', SYM, side, w.close_ms(5), Purpose.ENTRY)
    return ids.client_id_for(ids.derive_intent_id(w.config.account.account_id, key), 'classic')


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('filled', (True, False))
def test_new1_crash_between_the_reconcile_decision_and_its_result_never_wedges(side, filled):
    w = World(flat_bars(30), sig(side), strict=False)
    w.run(4)
    cid = _entry_cid(w, side)
    w.venue.not_found(cid, 99)                                     # the venue cannot find it by id for a long time
    if filled:
        w.venue.lose_next_market_answer('filled')                  # it filled; the answer is lost
    else:
        w.venue.lose_next_market_answer('not_filled')
    w.run(6)
    rec = []
    for i in range(7, 16):                                         # until the RECONCILE decision is durable
        t = w.close_ms(i)
        w.venue.advance_to(t)
        n = len(w.journal.read())
        w.runner.cycle(t)
        evs = w.journal.read()[n:]
        rec = [e for e in evs if isinstance(e, DecisionRecorded) and e.decision.action is Action.RECONCILE]
        if rec:
            break
    assert rec, 'the lost entry was never corroborated'
    # rebuild the store as if the process died right after the RECONCILE decision (its result never written)
    evs = w.journal.read()
    keep = evs[:evs.index(rec[0]) + 1]
    w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for e in keep:
        w.journal.append(e)
    w.runner = w.new_runner()
    w.runner.now = keep[-1].at_ms
    for i in range(i, 24):
        w.run(i)                                                   # no JournalConflict, every cycle
    r = w.runner
    entry, = [iv for iv in r.fold.intents.values() if iv.purpose is Purpose.ENTRY]
    assert not entry.live                                          # resolved, once
    assert sum(1 for d in r.fold.decisions.values() if d.action is Action.RECONCILE) == 1
    assert protected_end(w, side) and (bool(r.fold.open_lots()) == filled)


# ------------------------------------------------------------------------------------------------------- F1
@pytest.mark.parametrize('side', SIDES)
def test_f1_a_second_equal_gap_in_hard_hold_gets_its_own_emergency_stop(side):
    w = World(flat_bars(20), sig(side))
    w.run(6)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.venue.inject_position(SYM, side, D('3'), D('100'))           # +3: the first gap
    w.run(7)
    assert position(w, side) == covered(w, side) == D('8')
    w.venue.inject_position(SYM, side, D('3'), D('100'))           # +3 again: the same-size gap
    w.run(8)
    assert covered(w, side) >= position(w, side) == D('11')
    w.run(9)                                                       # idempotent: no duplicate, still covered
    assert covered(w, side) == D('11')


@pytest.mark.parametrize('side', SIDES)
def test_f2_a_cancelled_emergency_stop_id_is_not_reused_a_new_generation_covers(side):
    w = World(flat_bars(20), sig(side))
    w.run(6)
    w.journal.fail_writes(10 ** 9)
    w.runner.store_unavailable('test: ENOSPC')
    w.venue.inject_position(SYM, side, D('3'), D('100'))
    w.run(7)
    em = [o for o in w.venue.open_orders().value if ids.is_emergency_client_id(o.ref.client_id)]
    w.venue.external_cancel(em[0].ref.client_id)                    # the emergency stop vanishes at the venue
    w.run(8)
    assert covered(w, side) >= position(w, side) == D('8')


# ------------------------------------------------------------------------------------------------------- the 289
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('lost,later', [(1, False), (1, True), (2, False), (2, True)])
def test_fuzz289_an_executed_entry_whose_last_journal_events_were_lost_is_owned_and_protected(side, lost, later):
    """The entry reached the venue and filled; the process died before its result was journaled, and the store lost
    its last `lost` durable events too (the send record, the intent record...). Restart in the same candle or a later
    one: the entry is found by its deterministic client id, owned and protected - never an unowned naked position."""
    w = World(flat_bars(20), sig(side), strict=False)
    w.run(4)
    w.port.crash(1, 'after')                                       # the entry is the first venue effect
    t5 = w.close_ms(5)
    w.venue.advance_to(t5)
    with pytest.raises(Crash):
        w.runner.cycle(t5)
    w.port.disarm()
    evs = w.journal.read()
    keep = evs[:len(evs) - lost]
    # lost 3 = every record of the entry is gone: only a re-delivery in the same candle can own it (a later candle is the
    # contract item: REC-02 adopt / NC-01 A1)
    assert any(isinstance(e, DecisionRecorded) and e.decision.action is Action.ENTER for e in keep) == (lost < 3)
    w.journal = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for e in keep:
        w.journal.append(e)
    w.runner = w.new_runner()
    if not later:
        w.runner.cycle(t5)
    w.run(9)
    r = w.runner
    assert position(w, side) == D('5')
    lot, = r.fold.open_lots()
    assert lot.qty == D('5') and lot.live_stop is not None and lot.live_stop.state is IntentState.WORKING
    assert covered(w, side) >= D('5')


# The former 'every record lost -> surfaced HOLD' contract case is superseded by the recovery of Cowork 6062740390
# (test_cowork_recheck.py: lost 3 is owned and protected; only a position nothing proves ours stays an owner HOLD).
