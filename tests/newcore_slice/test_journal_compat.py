"""Codex ruling on the journal change (accepted only with compatibility): journals written in the last accepted shapes
restart identically, and a record this build cannot read exactly is a HOLD - never a crash, never a reinterpretation.

- v2 management ticks (35cb80c: 'mg tick <open> <o> <h> <l> <c> <request>', no ATR) and v3 (with ATR), alone or mixed in
  one journal: the restart folds the identical driver state;
- the pre-N6 exit order (stop cancelled, THEN the close: every journal before 8365d9a): the restart folds the same lots
  and the run goes on to the same trades;
- the pre-35cb80c tick ('mg tick <open> <request>': it needed a bars read on replay) and malformed records: the lot
  leaves management (the runner protects it), an incident, a durable HOLD; the boot does not raise.
(The NC-01 freeze itself - OrderIntent.replaces_intent_id - is the domain codec's: a pre-freeze record is rejected by
the strict schema-1 codec, the store answers DAMAGED and the run becomes the tail-loss guard; a migration / schema
bump is the NC-01 / NC-02 lane's.)"""
import dataclasses
from decimal import Decimal as D

import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import (Action, Authority, DecisionRecorded, EntriesMode, IntentState, Purpose, ReasonCode)
from newcore.runner import InjectedSignals, ids
from newcore.runner.managed import ManagedRunner, SyntheticPlans
from newcore.runner.records import planned_intent
from newcore.runner.runner import Runner
from mg_helpers import ZERO_COSTS, MgWorld, path, signals as mg_signals
from slice_helpers import ACCOUNT_ID, H4, PORTFOLIO_ID, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SIDES = ('LONG', 'SHORT')
FLOW = {6: (100, 100.2, 98.9, 99), 7: (99, 99.2, 98.9, 99), 8: (99, 102.2, 98.9, 102)}
PLANS = SyntheticPlans(costs=ZERO_COSTS, time_exit_candles=8)


def rewrite(w, fn):
    """A copy of the journal with every decision's detail passed through fn(detail) (a journal written in another
    shape); returns the new journal."""
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for ev in w.journal.read():
        if isinstance(ev, DecisionRecorded):
            new = fn(ev.decision.detail)
            if new != ev.decision.detail:
                ev = dataclasses.replace(ev, decision=dataclasses.replace(ev.decision, detail=new))
        j.append(ev)
    return j


def boot(w, journal):
    return ManagedRunner(w.config, journal=journal, venue=w.port, bars=w.bars, signals=w.signals,
                         account_reads=w.venue, management=w.management)


def to_v2(detail, every=1):
    if detail.startswith('mg tick '):
        p = detail.split(' ')
        if len(p) == 9 and int(p[2]) // H4 % every == 0:
            return ' '.join(p[:7] + p[8:])                         # drop the ATR token: the 35cb80c shape
    return detail


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('every', (1, 2))                          # 1: all v2; 2: v2 and v3 mixed
def test_v2_and_mixed_tick_journals_restart_identically(side, every):
    w = MgWorld(path(24, FLOW, side), mg_signals(side), plans=PLANS)
    w.run(12)
    old = w.runner
    j = rewrite(w, lambda d: to_v2(d, every))
    assert sum(1 for e in j.read() if isinstance(e, DecisionRecorded) and e.decision.detail.startswith('mg tick ')
               and len(e.decision.detail.split(' ')) == 8) >= 2
    new = boot(w, j)
    assert new.mg == old.mg and new.plans == old.plans and new.fold.mode is EntriesMode.ACTIVE


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('bad', ('pre35cb80c', 'garbage'))
def test_an_unreadable_management_record_is_a_hold_not_a_crash(side, bad):
    w = MgWorld(path(24, FLOW, side), mg_signals(side), plans=PLANS)
    w.run(9)

    def spoil(d):
        if d.startswith('mg tick '):
            p = d.split(' ')
            return f'mg tick {p[2]} -' if bad == 'pre35cb80c' else ' '.join(p[:3] + ['1.2.3'] + p[4:])
        return d
    j = rewrite(w, spoil)
    w.journal = j
    r = boot(w, j)                                                 # no exception at boot
    assert not r.mg and r.unmanaged                                # the lot left management at the bad record
    w.runner = r
    w.run(10)
    r = w.runner
    assert r.fold.mode is EntriesMode.HOLD and any('unreadable' in t for _, t in r.incidents)
    lot, = r.fold.open_lots()
    assert lot.carrier is not None and lot.carrier.state is IntentState.WORKING        # still protected


def _old_close_lot(self, lot, *, reason, key):
    """reconcile v0's pre-N6 _close_lot (8365d9a^): cancel the stop, then the close."""
    if key is not None:
        did, iid, authority = ids.derive_decision_id(self.acct, key), ids.derive_intent_id(self.acct, key), \
            Authority.STRATEGY
    else:
        iid = self.journal.gate().grammar.next_child_intent_id(lot.lot_id, Purpose.CLOSE)
        did, authority = ids.child_decision_id(iid), Authority.PROTECTION
    prior = self.journal.find_decision(did)
    if prior is not None:
        planned = prior.decision.intents[0]
    else:
        planned = planned_intent(intent_id=iid, account_id=self.acct, decision_id=did, purpose='close',
                                 symbol=lot.symbol, side=lot.side, qty=lot.qty, reason=reason, at_ms=self.now,
                                 owner_id=lot.lot_id)
        self._decision(decision_id=did, action=Action.CLOSE, reason=reason, authority=authority, key=key,
                       symbol=lot.symbol, side=lot.side, intents=(planned,), subject_id=lot.lot_id,
                       detail=f'close {lot.qty}')
    if planned.intent_id in self.fold.intents:
        return
    stop = lot.live_stop
    if stop is not None:
        if stop.state is not IntentState.CANCELLING:
            self._state(stop, IntentState.CANCELLING, reason)
        self._apply(stop, self.venue.cancel(self._ref(stop)), submit=False)
        if stop.live:
            self._apply(stop, self.venue.query(self._ref(stop)), submit=False)
        if stop.live:
            self._hold([ReasonCode.PROTECT_CHECKING])
            return
    lot = next((x for x in self.fold.open_lots() if x.lot_id == lot.lot_id), None)
    if lot is None:
        return
    self._send_close(self._record_durable(planned))


@pytest.mark.parametrize('side', SIDES)
def test_a_pre_n6_close_order_journal_restarts_identically(side, monkeypatch):
    s = {('SOLUSDT', flat_bars(1)[0].open_ms + 6 * H4): (('enter', side),),
         ('SOLUSDT', flat_bars(1)[0].open_ms + 11 * H4): (('close', side),),
         ('SOLUSDT', flat_bars(1)[0].open_ms + 14 * H4): (('enter', side),)}
    sig = InjectedSignals(s, stop_atr=D('2'))
    ref = World(flat_bars(20), sig)
    ref.run(17)
    w = World(flat_bars(20), sig)
    monkeypatch.setattr(Runner, '_close_lot', _old_close_lot)
    w.run(12)                                                      # the first exit is journaled in the OLD order
    monkeypatch.undo()
    evs = w.journal.read()                                         # (the old order is in the journal)
    close = next(iv for iv in w.runner.fold.intents.values() if iv.purpose is Purpose.CLOSE)
    stop = w.runner.fold.lots()[0].protects[0]
    cancel_at = next(e.sequence for e in evs if getattr(e, 'intent_id', None) == stop.intent_id
                     and getattr(e, 'to_state', None) is IntentState.CANCELLING)
    close_at = next(e.sequence for e in evs if getattr(getattr(e, 'intent', None), 'intent_id', None) == close.intent_id)
    assert cancel_at < close_at
    old = w.runner
    new = w.restart()
    assert [(x.lot_id, x.qty, x.open) for x in new.fold.lots()] == [(x.lot_id, x.qty, x.open) for x in old.fold.lots()]
    w.run(17)                                                      # and the run goes on with the current build
    assert [(t.exit_reason, t.exit_price) for t in w.runner.trades()] == \
        [(t.exit_reason, t.exit_price) for t in ref.runner.trades()]
    assert w.runner.counters.unprotected_cycles == 0
