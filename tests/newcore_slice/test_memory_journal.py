"""MemoryJournal beyond the step-0 contract (tests/newcore_ports/test_memory_journal_contract.py): the crash-point
injector, reopen, find_decision and the gate it exposes. Events are real NC-01 events written by the Runner (an entry
and its protective stop), replayed into fresh journals."""
import pytest

from newcore.adapters import MemoryJournal
from newcore.domain import DecisionRecorded, Purpose
from newcore.ports import Admission, JournalPort, JournalUnavailable, claim_signal
from newcore.ports import keys as K
from slice_helpers import ACCOUNT_ID, PORTFOLIO_ID, Crash, World, flat_bars
from test_runner_e2e import signals


@pytest.fixture(scope='module')
def trade():
    w = World(flat_bars(20), signals(exit_=None))
    w.run(5)                                                       # entry filled + stop working: 10 events
    lot, = w.runner.fold.open_lots()
    entry_key = w.runner.fold.entry_key(lot.entry)
    return list(w.journal.read()), lot.lot_id, entry_key


def test_is_the_port_and_exposes_its_gate(trade):
    events, lot, key = trade
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    assert isinstance(j, JournalPort)
    for ev in events:
        assert j.append(ev) is Admission.APPLY
    assert j.gate().grammar.next_child_intent_id(lot, Purpose.PROTECT) == K.derive_child_intent_id(
        ACCOUNT_ID, lot, Purpose.PROTECT, 1)
    assert not claim_signal(j.gate().grammar, key).fresh
    first = j.find_decision(K.derive_decision_id(ACCOUNT_ID, key))
    assert isinstance(first, DecisionRecorded) and first is events[0]


@pytest.mark.parametrize('error', [None, Crash])
def test_fail_writes_after_k_is_a_crash_point_that_lands_nothing(trade, error):
    events, _, _ = trade
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    j.fail_writes(1, after=3, error=error)
    for ev in events[:3]:
        assert j.append(ev) is Admission.APPLY
    with pytest.raises(error or JournalUnavailable):
        j.append(events[3])
    assert j.last_sequence() == 3
    assert j.append(events[3]) is Admission.APPLY                  # the store is back: the same event lands once


def test_reopen_rebuilds_the_gate_from_the_durable_events(trade):
    events, lot, _ = trade
    j = MemoryJournal(ACCOUNT_ID, PORTFOLIO_ID)
    for ev in events:
        j.append(ev)
    r = j.reopen()
    assert r.read() == j.read() and r.last_sequence() == j.last_sequence()
    assert all(r.append(ev) is Admission.ALREADY_APPLIED for ev in events)
    assert r.gate().grammar.next_child_intent_id(lot, Purpose.PROTECT) == \
        j.gate().grammar.next_child_intent_id(lot, Purpose.PROTECT)
