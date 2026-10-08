"""Hard HOLD (store cannot write, JournalUnavailable): exactly the NC-02 A23 / A24 emergency set, rows M43-M53
(docs/newcore/nc02_prep/nc02_acceptance_draft.md r4 on origin/prep/nc02-negative-fixtures), plus a crash matrix in this
mode. Nothing here is journaled: every cycle and every restart re-derives the set from exchange truth; deterministic
client ids (zbn1e- emergency stops, the intents' own ids for drains) make it idempotent."""
from decimal import Decimal as D

import pytest

from newcore.domain import EntriesMode, IntentState, Purpose
from newcore.ports.venue import OrderOutcome, OutcomeKind
from newcore.runner import InjectedSignals
from newcore.runner import ids
from slice_helpers import H4, Crash, World, flat_bars

SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
DOWN = 'test: ENOSPC'


def sig(*items):
    """items: (bar, action, side)."""
    out = {}
    for bar, action, side in items:
        out.setdefault((SYM, T0 + (bar + 1) * H4), []).append((action, side))
    return InjectedSignals({k: tuple(v) for k, v in out.items()}, stop_atr=D('2'))


def store_down(w):
    w.journal.fail_writes(10 ** 9)                                 # every later write fails
    w.runner.store_unavailable(DOWN)


def emergency_stops(w):
    return [o for o in w.venue.orders_submitted() if ids.is_emergency_client_id(o.ref.client_id)]


def resting_entry(w):
    return next(o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and not o.reduce)


def position(w, side='LONG'):
    return next(p.qty for p in w.venue.positions().value if p.side == side)


def covered(w, side='LONG'):
    return sum((o.qty for o in w.venue.open_orders().value if o.reduce and o.position_side == side), D(0))


def frozen(w):
    """What hard HOLD must never change: the durable journal."""
    return len(w.journal.read())


# ------------------------------------------------------------------------------------------------ M43-M46: protection
def test_m43_naked_position_gets_one_emergency_stop_and_hard_hold():
    w = World(flat_bars(20), sig((5, 'enter', 'LONG'), (12, 'enter', 'LONG')))
    w.run(5)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)  # the stop is gone
    w.journal.fail_writes(10 ** 9)                                 # ... and the store is down: the sync write fails
    n = frozen(w)
    w.run(14)
    r = w.runner
    assert r.hard_hold is not None and r.summary().mode == 'hold(durability_unavailable)'
    es, = emergency_stops(w)
    assert es.ref.client_id == ids.emergency_stop_client_id(r.acct, SYM, 'LONG', D('5'))
    assert (es.qty, es.stop_price, es.status) == (D('5'), lot.protects[0].intent.stop_price, 'NEW')
    assert frozen(w) == n and r.incidents and r.counters.unprotected_cycles == 0
    assert len([o for o in w.venue.orders_submitted() if o.order_type == 'MARKET']) == 1   # M44: entry12 refused


def test_m44_entries_are_refused_and_nothing_is_sent():
    w = World(flat_bars(20), sig((5, 'enter', 'LONG')))
    w.run(4)
    w.journal.fail_writes(10 ** 9)
    w.run(8)                                                       # the ENTER decision cannot be journaled
    assert w.runner.hard_hold is not None and w.venue.orders_submitted() == ()


def test_m45_confirmed_protection_is_left_unchanged():
    w = World(flat_bars(20), sig((5, 'enter', 'LONG')))
    w.run(5)
    effects = len(w.port.effects)
    store_down(w)
    w.run(10)
    assert len(w.port.effects) == effects                          # no exchange write at all
    assert emergency_stops(w) == [] and w.runner.incidents


def test_m46_uncovered_quantity_is_topped_up_and_old_protection_never_cancelled():
    w = World(flat_bars(20), sig((5, 'enter', 'LONG')))
    w.run(5)
    old = w.runner.fold.open_lots()[0].live_stop.intent.client_order_id
    w.venue.inject_position(SYM, 'LONG', D('3'), D('100'))         # the owned side grew (race / adopted fill)
    store_down(w)
    w.run(7)
    es, = emergency_stops(w)
    assert es.qty == D('3') and covered(w) == position(w) == D('8')
    assert w.venue._orders[old].status == 'NEW'                     # the old stop was never cancelled


# ------------------------------------------------------------------------------------------------ M47-M52: drain
def resting_world(*extra):
    w = World(flat_bars(20), sig((5, 'enter', 'LONG'), *extra))
    w.venue.rest_next_entries(1)
    w.run(5)                                                       # the entry rests: WORKING, HOLD (unconfirmed)
    entry = next(iv for iv in w.runner.fold.intents.values() if iv.purpose is Purpose.ENTRY)
    assert entry.state is IntentState.WORKING and w.runner.fold.mode is EntriesMode.HOLD
    return w, entry


def test_m47_resting_entry_is_cancelled_by_its_client_id_and_requeried():
    w, entry = resting_world()
    n = frozen(w)
    store_down(w)
    w.run(8)
    o = resting_entry(w)
    assert o.ref.client_id == entry.intent.client_order_id and o.status == 'CANCELED' and o.executed == 0
    assert w.venue.calls['cancel'] == 1                            # done once: the re-query shows it final
    assert len([x for x in w.venue.orders_submitted() if x.order_type == 'MARKET']) == 1   # no reprice / re-post
    assert emergency_stops(w) == [] and frozen(w) == n


def test_m48_cancel_already_gone_counts_as_done_without_a_retry_storm():
    w, entry = resting_world()
    store_down(w)
    cid = entry.intent.client_order_id

    def gone(ref):                                                 # the order vanished between query and cancel
        w.venue.external_cancel(cid)
        return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=w.venue.now_ms, error_code=-2011)
    w.port.inner.cancel = gone
    w.run(10)
    assert w.port.effects.count('cancel') == 1                     # never re-cancelled
    assert position(w) == 0 and emergency_stops(w) == []


@pytest.mark.parametrize('truth', ['working', 'cancelled'])
def test_m49_unknown_cancel_is_requeried_and_recancelled_only_while_working(truth):
    w, entry = resting_world()
    w.venue.lose_next_cancel_answer(entry.intent.client_order_id, truth)
    store_down(w)
    w.run(6)                                                       # cancel -> UNKNOWN
    w.run(8)                                                       # re-query; re-cancel only if still working
    assert resting_entry(w).status == 'CANCELED'
    assert w.port.effects.count('cancel') == (2 if truth == 'working' else 1)
    assert w.port.effects.count('market_entry') == 1               # never re-sent as a new entry


def test_m50_partial_fill_before_the_cancel_is_adopted_and_protected_exactly():
    w, entry = resting_world()
    w.venue.fill_resting(entry.intent.client_order_id, D('2'))
    store_down(w)
    w.run(7)
    o = resting_entry(w)
    assert o.status == 'CANCELED' and o.executed == D('2')           # the remainder is cancelled, never re-posted
    es, = emergency_stops(w)
    assert es.qty == D('2') == position(w)                           # exactly the adopted quantity, reduce-only
    assert es.stop_price == D('98.02')                               # 100.02 fill - 2 x ATR, never tighter
    assert w.runner.fold.mode is EntriesMode.HOLD and w.runner.hard_hold


def test_m51_fill_after_the_cancel_wins_and_is_protected():
    w, entry = resting_world()
    w.venue.fill_when_cancelled(entry.intent.client_order_id)
    store_down(w)
    w.run(7)
    assert resting_entry(w).status == 'FILLED' and position(w) == D('5')
    es, = emergency_stops(w)
    assert es.qty == D('5')
    assert not [o for o in w.venue.orders_submitted() if o.order_type == 'MARKET' and o.reduce]   # no market close


def test_m52_protection_stays_while_another_side_is_drained():
    w = World(flat_bars(20), sig((5, 'enter', 'LONG'), (6, 'enter', 'SHORT')))
    w.run(5)
    stop = w.runner.fold.open_lots()[0].live_stop.intent.client_order_id
    w.venue.rest_next_entries(1)
    w.run(6)                                                       # the SHORT entry rests
    store_down(w)
    w.run(9)
    short = [o for o in w.venue.orders_submitted() if o.position_side == 'SHORT']
    assert [o.status for o in short] == ['CANCELED']
    assert w.venue._orders[stop].status == 'NEW' and emergency_stops(w) == []


# ------------------------------------------------------------------------------------------------ M53: restarts
def test_m53_restart_mid_drain_repeats_from_exchange_truth_idempotently():
    w, entry = resting_world()
    w.venue.fill_resting(entry.intent.client_order_id, D('2'))
    store_down(w)
    w.port.crash(len(w.port.effects) + 2, 'after')                 # dies right after placing the emergency stop
    with pytest.raises(Crash):
        w.run(7)
    for _ in range(2):                                             # two restarts, the store still down
        w.restart(hard_hold=DOWN)
        w.runner.cycle(w.close_ms(7))
        assert w.runner.hard_hold is not None
    w.run(9)
    es, = emergency_stops(w)                                       # found by its id, never duplicated
    assert es.qty == D('2') and covered(w) == position(w) == D('2')
    assert resting_entry(w).status == 'CANCELED' and w.port.effects.count('cancel') == 1


def test_store_back_hands_over_to_journaled_protection_then_resume():
    w = World(flat_bars(20), sig((5, 'enter', 'LONG')))
    w.run(5)
    lot, = w.runner.fold.open_lots()
    w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    w.journal.fail_writes(10 ** 9)
    w.run(6)
    es, = emergency_stops(w)
    w.restart()                                                    # the store is writable again
    w.run(7)
    r = w.runner
    lot, = r.fold.open_lots()
    assert lot.live_stop.state is IntentState.WORKING              # journaled protection confirmed first ...
    assert w.venue._orders[es.ref.client_id].status == 'CANCELED'  # ... then the emergency stop is retired
    assert covered(w) == position(w) == D('5') and r.fold.mode is EntriesMode.HOLD
    w.run(8)
    assert r.resume(w.close_ms(8)) and r.fold.mode is EntriesMode.ACTIVE


# ------------------------------------------------------------------------------------------------ crash matrix
def hh_scenario(kind):
    if kind == 'naked':
        w = World(flat_bars(20), sig((5, 'enter', 'LONG')))
        w.run(5)
        w.venue.external_cancel(w.runner.fold.open_lots()[0].live_stop.intent.client_order_id)
        return w, D('5')
    w, entry = resting_world()
    if kind == 'partial':
        w.venue.fill_resting(entry.intent.client_order_id, D('2'))
        return w, D('2')
    if kind == 'race':
        w.venue.fill_when_cancelled(entry.intent.client_order_id)
        return w, D('5')
    return w, D('0')                                               # 'resting': a clean cancel


def hh_effects(kind):
    w, _ = hh_scenario(kind)
    n = len(w.port.effects)
    store_down(w)
    w.run(9)
    return len(w.port.effects) - n


HH_MATRIX = [(k, n, when) for k in ('naked', 'resting', 'partial', 'race')
             for n in range(1, hh_effects(k) + 1) for when in ('before', 'after')]


@pytest.mark.parametrize('kind,n,when', HH_MATRIX)
def test_crash_matrix_in_hard_hold(kind, n, when):
    """The process dies before / after every exchange effect of the emergency set; it restarts with the store still
    down. The set must converge: drained, protected exactly once, no exposure increase, nothing journaled."""
    w, expect = hh_scenario(kind)
    base = len(w.port.effects)
    journal = frozen(w)
    store_down(w)
    w.port.crash(base + n, when)
    for i in range(6, 12):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        try:
            w.runner.cycle(t)
        except Crash:
            w.restart(hard_hold=DOWN)
            w.runner.cycle(t)
    assert position(w) == expect                                   # exposure never increased
    assert covered(w) >= position(w)                               # protected (or flat)
    cids = [o.ref.client_id for o in emergency_stops(w)]
    assert len(cids) == len(set(cids)) and len(cids) <= 1          # one emergency stop at most, never duplicated
    assert w.port.effects.count('market_entry') <= 1 and not [o for o in w.venue.orders_submitted()
                                                               if o.order_type == 'MARKET' and o.reduce]
    if kind != 'naked':
        assert resting_entry(w).status in ('CANCELED', 'FILLED')    # drained
    assert frozen(w) == journal


pytestmark = pytest.mark.usefixtures('journal_kind')            # every test: MemoryJournal and FileJournal


def test_the_file_parameter_really_runs_the_nc02a_file_journal(journal_kind):
    """Guard for the journal_kind parametrization: under "file" the Runner journals into NC-02a segment files."""
    import os
    w = World(flat_bars(20), sig((5, "enter", "LONG")))
    w.run(6)
    if journal_kind == "file":
        from file_journal_harness import FileJournalProxy
        assert isinstance(w.journal, FileJournalProxy)
        segs = [s for s in os.listdir(os.path.join(w.journal._j.account_dir, 'journal')) if s != '.lock']
        assert segs and all(s.endswith('.seg') for s in segs)             # (+ the NC-02a writer lock file)
        assert len(w.journal.reopen().read()) == len(w.journal.read()) > 0
