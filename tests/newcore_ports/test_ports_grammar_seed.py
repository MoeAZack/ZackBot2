"""GRAMMAR_SEED.md section 5 on the ports flows: every cut k of the contract flows (algo fallback, unknown-then-found,
never-sent, a late fill after not-found + its reconcile) restores the same gate state from the seed, and every BAD
probe of the JournalPort contract gets the same answer from the seeded gate as from the full-log gate."""
import pytest

from journal_contract import BAD, GOOD, _flow
from nc_events import ACCT, PF
from newcore.ports.journal import Admission, JournalConflict, JournalGate
from newcore.ports.seed import seed_bytes, seed_from_bytes
from test_ports_journal import _late_fill_decision_event, _late_fill_flow


def _late_fill_events():
    from newcore.domain import ReasonCode, ResultObserved
    s, sid, late = _late_fill_flow()
    s._event(ResultObserved, reason=ReasonCode.RECONCILE_LATE_FILL, result=late)
    _late_fill_decision_event(s, sid, late)
    return s.events


FLOWS = {name: _flow(fn).events for name, fn in GOOD.items()}
FLOWS['late_fill_reconciled'] = _late_fill_events()


def answer(gate, ev):
    try:
        st = gate.stage(ev)
    except JournalConflict:
        return 'conflict'
    return 'already_applied' if st is None or st is Admission.ALREADY_APPLIED else 'apply'


def restored(events, k):
    seed = JournalGate.rebuild(ACCT, PF, events[:k]).grammar_seed()
    seed = seed_from_bytes(seed_bytes(seed))
    return JournalGate.rebuild(ACCT, PF, events[k:], grammar_seed=seed)


@pytest.mark.parametrize('name', sorted(FLOWS))
def test_every_cut_restores_the_full_log_gate(name):
    events = FLOWS[name]
    whole = JournalGate.rebuild(ACCT, PF, events)
    for k in range(len(events) + 1):
        gate = restored(events, k)
        assert gate.grammar_seed() == whole.grammar_seed(), k
        for ev in events:                                     # G3: every old event again is a no-op on both
            assert answer(gate, ev) == answer(whole, ev) == 'already_applied', (k, ev.sequence)


@pytest.mark.parametrize('name', sorted(BAD))
def test_every_cut_refuses_what_the_full_log_refuses(name):
    fn, _ = BAD[name]
    prefix, bad = fn()
    prefix = list(prefix)
    want = answer(JournalGate.rebuild(ACCT, PF, prefix), bad)
    for k in range(len(prefix) + 1):
        assert answer(restored(prefix, k), bad) == want, k


def test_the_late_fill_seed_carries_the_superseded_state():
    events = FLOWS['late_fill_reconciled']
    seed = JournalGate.rebuild(ACCT, PF, events).grammar_seed()
    late = [si for si in seed.intents if si.superseded]
    assert len(late) == 1 and late[0].late_applied and late[0].late_result_id == late[0].final.result_id
