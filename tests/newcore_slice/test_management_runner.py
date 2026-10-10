"""M4: the ManagementDriver wired into the runner (newcore/runner/managed.py), on FakeVenue, long and short.

Flow: entry -> stop CONFIRMED -> add (only behind a confirmed stop) -> stop resized (old cancelled only after the new one
is confirmed) -> TP1 partial -> net break-even replace (same rule) -> time exit -> last stop released. Plus: a crash /
restart at every journal boundary and at every venue effect of that flow, restart = identical driver state, HOLD
suppresses the add / targets / management exits while protection and protective closes still go through, the three
Cowork driver cases end protected or in HOLD (never naked), management disabled = the plain runner, byte for byte, and
the RANGE-BB-MR.v1 preset as the disabled mechanics fixture."""
from decimal import Decimal as D

import pytest

from newcore.domain import (Action, DecisionRecorded, EntriesMode, IntentRecorded, IntentState, IntentStateChanged,
                            Purpose, ReasonCode, ResultObserved, ResultPhase)
from newcore.management import CostModel
from newcore.ports.keys import client_id_for, derive_child_intent_id
from newcore.runner import Runner, ids
from newcore.runner import config as C
from newcore.runner.managed import ManagedRunner, ManagementConfig, RangeFixturePlans, SyntheticPlans
from mg_helpers import MgWorld, SYM, ZERO_COSTS, intents_of, mg_decisions, path, signals
from slice_helpers import Crash

pytestmark = pytest.mark.usefixtures('journal_kind')            # MemoryJournal and FileJournal

SIDES = ('LONG', 'SHORT')
N = 24
LAST = 16
# long frame: entry fills at bar 6's open (100); bar 6 closes at the add level, bar 8 closes beyond TP1, then flat until
# the time exit (candle 8 from the entry candle = bar 13)
FLOW = {6: (100, 100.2, 98.9, 99), 7: (99, 99.2, 98.9, 99), 8: (99, 102.2, 98.9, 102)}
PLANS = SyntheticPlans(costs=ZERO_COSTS, time_exit_candles=8)


def world(side, bars=FLOW, plans=PLANS, **kw):
    return MgWorld(path(N, bars, side), signals(side), plans=plans, **kw)


def run_safely(w, upto, hooks=None):
    """Cycle to `upto`; on Crash: restart (journal reopened, gate rebuilt, drivers folded), re-run the same candle."""
    hooks = hooks or {}
    start = max(0, (w.venue.now_ms - w.t0) // w.config.tf_ms)
    for i in range(start, upto + 1):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        if i in hooks:
            hooks[i](w)
        try:
            w.runner.cycle(t)
        except Crash:
            w.port.disarm()
            w.restart()
            w.runner.cycle(t)
    return w.runner


def seq(w, pred):
    return [ev.sequence for ev in w.journal.read() if pred(ev)]


def first(w, pred):
    s = seq(w, pred)
    return s[0] if s else None


def result_of(iid, phase):
    return lambda ev: isinstance(ev, ResultObserved) and ev.result.intent_id == iid and ev.result.phase is phase


def cancelling(iid):
    return lambda ev: isinstance(ev, IntentStateChanged) and ev.intent_id == iid and \
        ev.to_state is IntentState.CANCELLING


def flat(w):
    return all(p.qty == 0 for p in w.venue.positions().value) and not w.venue.open_orders().value


def protected(r):
    lot, = r.fold.open_lots()
    c = lot.carrier
    return c is not None and c.state is IntentState.WORKING and c.intent.qty >= lot.qty


def trade_key(r):
    return [(t.exit_reason, t.entry_price, t.exit_price, t.qty) for t in r.trades()]


# ------------------------------------------------------------------------------------------------------------ flow
@pytest.mark.parametrize('side', SIDES)
def test_flow_entry_stop_add_resize_tp1_break_even_time_exit(side):
    w = world(side)
    r = run_safely(w, LAST)
    assert flat(w) and r.counters.unprotected_cycles == 0 and r.fold.mode is EntriesMode.ACTIVE
    stops = intents_of(r, Purpose.PROTECT)
    add, = intents_of(r, Purpose.ADD)
    tp1, = [iv for iv in intents_of(r, Purpose.REDUCE) if iv.intent.reason is ReasonCode.EXIT_TP1]
    close, = intents_of(r, Purpose.CLOSE)
    entry, = intents_of(r, Purpose.ENTRY)
    q = entry.executed
    lot_id = ids.derive_lot_id(r.acct, entry.intent_id)
    # every management intent is the lot's lineage child, in order, and every decision is a management decision
    assert [s.intent_id for s in stops] == [derive_child_intent_id(r.acct, lot_id, Purpose.PROTECT, i)
                                           for i in range(len(stops))]
    assert add.intent_id == derive_child_intent_id(r.acct, lot_id, Purpose.ADD, 0)
    assert len(mg_decisions(r)) == len(stops) + 3
    s0, s1, s2 = stops                                             # initial, resized after the add, break-even
    # the add is decided only after the first stop is CONFIRMED (KNOWN)
    assert first(w, result_of(s0.intent_id, ResultPhase.KNOWN)) < \
        first(w, lambda ev: isinstance(ev, DecisionRecorded) and ev.decision.decision_id == add.intent.decision_id)
    assert add.executed == q and s1.intent.qty == 2 * q and s1.intent.stop_price == s0.intent.stop_price
    # the old stop is cancelled only AFTER its replacement is confirmed
    assert first(w, result_of(s1.intent_id, ResultPhase.KNOWN)) < first(w, cancelling(s0.intent_id))
    assert first(w, result_of(s2.intent_id, ResultPhase.KNOWN)) < first(w, cancelling(s1.intent_id))
    # TP1 = half of the added position; then the net break-even stop on the rest (at / beyond the basket average)
    assert tp1.executed == q and s2.intent.qty == q
    avg = (entry.final.avg_price + add.final.avg_price) / 2
    assert (s2.intent.stop_price >= avg) if side == 'LONG' else (s2.intent.stop_price <= avg)
    # the time exit closes the rest; the last stop is released after the close filled
    assert close.intent.reason is ReasonCode.EXIT_TIME and close.executed == q
    assert first(w, result_of(close.intent_id, ResultPhase.FINAL)) < first(w, cancelling(s2.intent_id))
    t, = r.trades()
    assert t.exit_reason is ReasonCode.EXIT_TIME and t.qty == q
    assert str(r.portfolio().ownership) == 'known_empty'


@pytest.mark.parametrize('side', SIDES)
def test_an_unconfirmed_stop_holds_the_add(side):
    """The first stop's answer is lost and the venue cannot be read for it: the add level is crossed, but the driver
    arms the add only behind a CONFIRMED stop (and the naked lot puts the runner in HOLD). Once the stop is confirmed
    (same-id re-send) and the operator resumes, the add goes - decided after the stop's KNOWN result."""
    from newcore.management import driver as DR
    bars = {6: (100, 100.2, 98.9, 99), 7: (99, 99.2, 98.9, 99), 8: (99, 99.2, 98.9, 99)}
    w = world(side, bars, strict=False)
    acct = w.config.account.account_id
    key = ids.decision_key('injected', 'v1', '4h', SYM, side, w.close_ms(5), Purpose.ENTRY)
    lot_id = ids.derive_lot_id(acct, ids.derive_intent_id(acct, key))
    stop_id = derive_child_intent_id(acct, lot_id, Purpose.PROTECT, 0)
    cid = client_id_for(stop_id, 'classic')

    def lose(w):
        w.port.lose('stop')
        w.port.blind(cid)
    run_safely(w, 6, {5: lose})
    r = w.runner
    ds = r.mg[lot_id]
    assert r.fold.intents[stop_id].state is IntentState.UNKNOWN and DR.confirmed_coverage(ds) == 0
    assert all(t.leg.value != 'add' for t in DR.armed_triggers(ds))          # crossed at bar 6: not armed
    assert not intents_of(r, Purpose.ADD) and r.fold.mode is EntriesMode.HOLD

    def see(w):
        w.port._blind.discard(cid)
    run_safely(w, 7, {7: see})
    r = w.runner
    assert r.fold.intents[stop_id].state is IntentState.WORKING and DR.protected(r.mg[lot_id])
    assert not intents_of(r, Purpose.ADD)                                     # still HOLD
    assert r.resume(w.close_ms(7))
    run_safely(w, 8)
    r = w.runner
    add, = intents_of(r, Purpose.ADD)
    assert first(w, result_of(stop_id, ResultPhase.KNOWN)) < \
        first(w, lambda ev: isinstance(ev, DecisionRecorded) and ev.decision.decision_id == add.intent.decision_id)
    assert protected(r)


@pytest.mark.parametrize('side', SIDES)
def test_restart_folds_the_identical_driver_state_after_every_cycle(side):
    w = world(side)
    for i in range(LAST + 1):
        t = w.close_ms(i)
        w.venue.advance_to(t)
        w.runner.cycle(t)
        old = w.runner
        new = w.restart()
        assert new.mg == old.mg and new.plans == old.plans and new.unmanaged == old.unmanaged
    assert trade_key(w.runner) == trade_key(run_safely(world(side), LAST))


# --------------------------------------------------------------------------------------------------- crash matrix
def _reference(side):
    w = world(side)
    run_safely(w, LAST)
    return w


REF_EVENTS = {s: len(_reference(s).journal.read()) for s in SIDES}
REF_EFFECTS = {s: len(_reference(s).port.effects) for s in SIDES}


@pytest.mark.parametrize('side,k', [(s, k) for s in SIDES for k in range(REF_EVENTS[s])])
def test_crash_at_every_journal_boundary(side, k):
    """The process dies at the k-th journal write of the flow (the event never becomes durable); the restart folds the
    journal (drivers included) and re-runs the candle. Never naked (strict), one add, same trade, flat, ACTIVE."""
    ref = _reference(side)
    w = world(side)
    w.journal.fail_writes(1, after=k, error=Crash)
    r = run_safely(w, LAST)
    assert r.counters.unprotected_cycles == 0
    assert flat(w) and r.fold.mode is EntriesMode.ACTIVE
    assert len([iv for iv in intents_of(r, Purpose.ADD) if iv.executed > 0]) == 1
    assert trade_key(r) == trade_key(ref.runner)
    cids = r.fold.client_ids_recorded
    assert len(cids) == len(set(cids))


@pytest.mark.parametrize('side,n,when', [(s, n, wh) for s in SIDES for n in range(1, REF_EFFECTS[s] + 1)
                                          for wh in ('before', 'after')])
def test_crash_at_every_venue_effect(side, n, when):
    """The process dies just before / just after the n-th venue effect (submit / cancel). Never naked; at most one
    add executed; no client id reused; the run ends flat (a lost add is resolved by corroboration, then HOLD)."""
    w = world(side, strict=False)
    w.port.crash(n, when)
    r = run_safely(w, LAST)
    assert r.counters.unprotected_cycles == 0
    assert len([iv for iv in intents_of(r, Purpose.ADD) if iv.executed > 0]) <= 1
    cids = r.fold.client_ids_recorded
    assert len(cids) == len(set(cids))
    if r.fold.open_lots():
        assert protected(r)
    else:
        assert all(p.qty == 0 for p in w.venue.positions().value)


# ----------------------------------------------------------------------------------------------------------- HOLD
@pytest.mark.parametrize('side', SIDES)
def test_hold_suppresses_add_targets_and_time_exit_until_resume(side):
    bars = {6: (100, 100.2, 98.9, 99), 7: (99, 103.2, 98.9, 103)}       # add level, then beyond TP1 (no add: 102.02)
    w = world(side, bars)

    def hold(w):
        w.runner._hold([ReasonCode.RECONCILE_UNRECONCILED])
    run_safely(w, 14, {6: hold})
    r = w.runner
    assert r.fold.mode is EntriesMode.HOLD
    assert not intents_of(r, Purpose.ADD) and not intents_of(r, Purpose.REDUCE) and not intents_of(r, Purpose.CLOSE)
    assert protected(r) and r.counters.unprotected_cycles == 0
    assert len([d for d in r.fold.decisions.values() if d.action is Action.WAIT and d.detail.startswith('mg tick')]) >= 8
    assert r.resume(w.close_ms(14))                                   # operator RESUME: the held time exit goes
    run_safely(w, 17)
    r = w.runner
    close, = intents_of(r, Purpose.CLOSE)
    assert close.intent.reason is ReasonCode.EXIT_TIME and close.intent.created_at_ms > w.close_ms(14)
    assert flat(w) and not intents_of(r, Purpose.ADD)


@pytest.mark.parametrize('side', SIDES)
def test_hold_lets_protection_and_a_protective_close_through(side):
    """HOLD (Cowork MED-2, deliberate update): a lot covered by a CONFIRMED stop keeps it - the trail move is not sent
    and no second stop appears (it used to be placed with the old one kept: a pile of stops). Protection itself is
    never held: when the stop vanishes, its restore is sent at once; when the venue refuses it, the protective
    stop-failed close goes through. On resume nothing is left resting."""
    plans = SyntheticPlans(costs=ZERO_COSTS, add_r=None, tp1_r=None, tp2_r=None, be_after_tp1=False,
                           time_exit_candles=None, trail_r=D('0.5'))
    bars = {6: (100, 101.2, 99.9, 101), 7: (101, 102.2, 100.9, 102)}
    w = world(side, bars, plans=plans)

    def hold(w):
        w.runner._hold([ReasonCode.RECONCILE_UNRECONCILED])

    def vanish_and_refuse(w):
        lot, = w.runner.fold.open_lots()
        w.venue.external_cancel(lot.carrier.intent.client_order_id)     # the stop is gone at the venue ...
        w.port.refuse('stop', -2021)                                     # ... and its restore is refused
    run_safely(w, 6, {6: hold})
    r = w.runner
    s0, = intents_of(r, Purpose.PROTECT)                                 # the trail move was NOT sent in HOLD
    assert s0.state is IntentState.WORKING and protected(r) and r.fold.mode is EntriesMode.HOLD
    run_safely(w, 7, {7: vanish_and_refuse})
    r = w.runner
    close, = intents_of(r, Purpose.CLOSE)
    assert close.intent.reason is ReasonCode.EXIT_STOP_FAILED and close.executed > 0
    assert all(p.qty == 0 for p in w.venue.positions().value) and r.fold.mode is EntriesMode.HOLD
    w.port._refuse.clear()
    assert r.resume(w.close_ms(7))
    run_safely(w, 9)
    assert flat(w)


# ------------------------------------------------------------------------------------- Cowork driver cases (M4 HIGHs)
@pytest.mark.parametrize('side', SIDES)
def test_a_stop_cancelled_outside_the_bot_ends_protected(side):
    w = world(side)

    def kill(w):
        lot, = w.runner.fold.open_lots()
        w.venue.external_cancel(lot.carrier.intent.client_order_id)
    run_safely(w, 9, {9: kill})
    r = w.runner
    assert r.counters.unprotected_cycles == 0
    assert protected(r)                                               # re-protected in the same cycle
    run_safely(w, LAST)
    assert w.runner.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
def test_a_partially_filled_stop_ends_protected_or_in_hold(side):
    w = world(side)

    def partial(w):
        lot, = w.runner.fold.open_lots()
        o = w.venue._orders[lot.carrier.intent.client_order_id]
        w.venue._execute_part(o, lot.qty / 2, o.stop_price, at_ms=w.venue.now_ms)
    run_safely(w, 9, {9: partial})
    r = w.runner
    assert r.counters.unprotected_cycles == 0
    pos = sum((p.qty for p in w.venue.positions().value), D(0))
    lot, = r.fold.open_lots()
    assert r.fold.mode is EntriesMode.HOLD                            # venue position != owned
    assert lot.carrier.state is IntentState.WORKING and lot.carrier.intent.qty >= pos
    run_safely(w, LAST)
    assert w.runner.counters.unprotected_cycles == 0


@pytest.mark.parametrize('side', SIDES)
def test_a_late_rejected_after_flat_is_contained(side):
    """The TP1 reduce never reached the venue (crash before the call); after the restart the stop fills (flat), then the
    reduce is re-sent under its id and refused (nothing to reduce). The driver gets a REJECTED for a position it already
    closed: no crash, nothing naked, the run stays flat (HOLD when the driver refuses the late answer)."""
    bars = dict(FLOW)
    bars[9] = (95, 95.2, 94.8, 95)                                    # through every stop at the open
    ref = world(side, bars)
    run_safely(ref, 7)
    before = len(ref.port.effects)
    run_safely(ref, 8)
    kinds = ref.port.effects[before:]
    n = before + kinds.index('market_reduce') + 1                     # the TP1 reduce effect
    w = world(side, bars, strict=False)
    run_safely(w, 7)
    w.port.crash(n, 'before')
    t8 = w.close_ms(8)
    w.venue.advance_to(t8)
    with pytest.raises(Crash):
        w.runner.cycle(t8)
    w.port.disarm()
    w.restart()
    run_safely(w, 12, )
    r = w.runner
    assert r.counters.unprotected_cycles == 0
    assert all(p.qty == 0 for p in w.venue.positions().value)
    tp1, = [iv for iv in intents_of(r, Purpose.REDUCE) if iv.intent.reason is ReasonCode.EXIT_TP1]
    assert not tp1.live and tp1.executed == 0                         # resolved by the venue's refusal


# ------------------------------------------------------------------------------------------- disabled / fixture
def _events(w):
    return [(type(ev).__name__, ev) for ev in w.journal.read()]


@pytest.mark.parametrize('side', SIDES)
def test_management_disabled_is_the_plain_runner_byte_for_byte(side):
    bars = {6: (100, 100.2, 98.9, 99), 9: (99, 99.2, 94, 95)}

    class Plain(MgWorld):
        def new_runner(self, hard_hold=None):
            return Runner(self.config, journal=self.journal, venue=self.port, bars=self.bars, signals=self.signals,
                          account_reads=self.venue, hard_hold=hard_hold)
    a = Plain(path(N, bars, side), signals(side, close_bar=12))
    b = MgWorld(path(N, bars, side), signals(side, close_bar=12), enabled=False)
    c = MgWorld(path(N, bars, side), signals(side, close_bar=12), plans=lambda info: None)   # enabled, no plan
    for x in (a, b, c):
        run_safely(x, LAST)
    assert isinstance(b.runner, ManagedRunner) and not b.runner.mg and not c.runner.mg
    assert _events(a) == _events(b) == _events(c)
    assert trade_key(a.runner) == trade_key(b.runner) == trade_key(c.runner)


@pytest.mark.parametrize('side', SIDES)
def test_range_bb_mr_v1_preset_is_the_disabled_mechanics_fixture(side):
    costs = CostModel(taker_fee=D('0.0005'), slip=D('0.0002'))
    bars = {6: (100, 100.2, 98.9, 99), 8: (99, 103.2, 98.9, 103)}
    w = world(side, bars, plans=RangeFixturePlans(costs=costs))
    r = run_safely(w, 22)
    plan, = r.plans.values()
    assert plan.disabled_by_default and plan.mechanics_only and plan.candle_seconds == 4 * 3600
    entry, = intents_of(r, Purpose.ENTRY)
    d = r._entry_distance(entry)
    assert abs(plan.stop_price - entry.final.avg_price) >= d - D('1e-9')         # 2 x ATR0 = the sized distance
    assert plan.has_add and plan.time_exit_candles == 12
    assert r.counters.unprotected_cycles == 0 and flat(w)
    assert intents_of(r, Purpose.ADD) and r.trades()


# ------------------------------------------------------------------------------------------------------- config
def test_management_config_defaults_off_and_is_validated(tmp_path):
    base = {'journal': {'dir': str(tmp_path / 'j')}}
    cfg = C.validate(base, environ={})
    assert cfg.mg_enabled is False and cfg.mg_plan == 'range_bb_mr_v1'
    on = C.validate({**base, 'management': {'enabled': True}}, environ={})
    assert on.mg_enabled and on.mg_cap_mult == D('2.5')
    for bad in ({'management': {'enabled': 'yes'}}, {'management': {'plan': 'grid'}},
                {'management': {'enabled': True}, 'strategy': {'tf': '1h'}}, {'management': {'cap_mult': '50'}},
                {'management': {'trail': '1'}}):
        with pytest.raises(C.ConfigError):
            C.validate({**base, **bad}, environ={})
    m = ManagementConfig()
    assert m.enabled is False


def test_client_ids_of_management_intents_are_classic_lineage_ids():
    w = world('LONG')
    r = run_safely(w, LAST)
    for iv in r.fold.intents.values():
        if iv.purpose is not Purpose.ENTRY:
            assert iv.intent.client_order_id == client_id_for(iv.intent_id, 'classic')
            assert isinstance(iv.intent, type(intents_of(r, Purpose.ENTRY)[0].intent))
    assert not [ev for ev in w.journal.read() if isinstance(ev, IntentRecorded) and ev.intent.owner_id is not None
                and ev.intent.owner_kind is None]
