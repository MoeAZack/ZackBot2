"""MemoryJournal beyond the step-0 contract (tests/newcore_ports/test_memory_journal_contract.py): the crash-point
injector, reopen, find_decision and the gate it exposes."""
import pytest

from journal_contract import _flow, flow_fill_and_protect
from nc_events import ACCT, KEY, LOT, PF
from newcore.adapters import MemoryJournal
from newcore.domain import Purpose
from newcore.ports import Admission, JournalPort, JournalUnavailable, claim_signal
from newcore.ports import keys as K


def test_is_the_port_and_exposes_its_gate():
    j = MemoryJournal(ACCT, PF)
    assert isinstance(j, JournalPort)
    s = _flow(flow_fill_and_protect)
    for ev in s.events:
        j.append(ev)
    assert j.gate().grammar.next_child_intent_id(LOT, Purpose.PROTECT) == K.derive_child_intent_id(
        ACCT, LOT, Purpose.PROTECT, 1)
    assert not claim_signal(j.gate().grammar, KEY).fresh
    assert j.find_decision(K.derive_decision_id(ACCT, KEY)) is s.events[0]


def test_fail_writes_after_k_is_a_crash_point_that_lands_nothing():
    s = _flow(flow_fill_and_protect)
    j = MemoryJournal(ACCT, PF)
    j.fail_writes(1, after=3)
    for ev in s.events[:3]:
        assert j.append(ev) is Admission.APPLY
    with pytest.raises(JournalUnavailable):
        j.append(s.events[3])
    assert j.last_sequence() == 3
    assert j.append(s.events[3]) is Admission.APPLY             # the store is back


def test_reopen_rebuilds_the_gate_from_the_durable_events():
    s = _flow(flow_fill_and_protect)
    j = MemoryJournal(ACCT, PF)
    for ev in s.events:
        j.append(ev)
    r = j.reopen()
    assert r.read() == j.read() and r.last_sequence() == j.last_sequence()
    assert all(r.append(ev) is Admission.ALREADY_APPLIED for ev in s.events)
    assert r.gate().grammar.next_child_intent_id(LOT, Purpose.PROTECT) == \
        j.gate().grammar.next_child_intent_id(LOT, Purpose.PROTECT)
