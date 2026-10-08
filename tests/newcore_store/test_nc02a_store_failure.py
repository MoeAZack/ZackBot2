"""Store-failure drill (A21 / A23 / A24), lost-answer drill and the HOLD mapping.

A failed write / fsync raises DurabilityUnavailable, poisons the journal (never retried), books nothing; a restart
finds the event absent, torn (sealed) or complete, never anything else. The pure helpers map it to NC-01's hard HOLD
and its emergency set. A sent intent whose answer was lost recovers as UNKNOWN (query the venue), never as filled or
never-filled; a bare not-found keeps it UNKNOWN."""
import os
from decimal import Decimal as D

import pytest

from nc02a_events import (ACCOUNT_ID, AGGREGATE_ID, INTENTS, SCENARIO, T0, ev, hold_event, lost_answer_events, result,
                          state)
from nc02a_memfs import PENDING, FaultFs, MemFs, oserror
from nc02a_util import ACCT_DIR, mem_journal, recover, seg
from newcore.domain import (EntriesMode, Evidence, HoldKind, IntentState, Op, Permission, Purpose, ReasonCode,
                            ResultObserved, ResultPhase)
from newcore.ports.journal import Admission, JournalUnavailable
from newcore.store import (DurabilityUnavailable, IntentRecovery, StoreOutcome, Verdict, create_journal,
                           directive_for, durability_hold, hard_hold_permits)

ENOSPC, EROFS, EACCES = 28, 30, 13


def _journal_with(n, fs=None):
    fs = mem_journal(SCENARIO[:n], fs)
    r = recover(fs)
    assert r.verdict is Verdict.CLEAN
    return fs, r.journal


def _fail_on(op_name, code):
    return lambda op, p, i: oserror(code) if op == op_name else None


# ------------------------------------------------------------------------------------------------ store failure
@pytest.mark.parametrize('op_name,code', [('write', ENOSPC), ('write', EROFS), ('fsync', ENOSPC), ('fsync', EACCES)])
def test_persistently_failing_store_raises_consumes_nothing_and_a_retry_lands_once_when_writable(op_name, code):
    fs, j = _journal_with(5)
    f = FaultFs(fs, fail=_fail_on(op_name, code))
    j._fs = f
    gate_before = j.gate().grammar.last_sequence
    with pytest.raises(DurabilityUnavailable) as ex:
        j.append(SCENARIO[5])
    assert isinstance(ex.value, JournalUnavailable) and ex.value.errno == code and ex.value.op == op_name
    assert ACCT_DIR not in str(ex.value)                              # names the file, never the path
    assert j.needs_roll == (op_name == 'fsync')                     # a failed fsync leaves bytes to seal off
    assert j.last_sequence() == 5 and j.read() == tuple(SCENARIO[:5])
    assert j.gate().grammar.last_sequence == gate_before              # staged, never committed: nothing consumed
    assert j.find_decision(SCENARIO[5].decision.decision_id) is None
    for e in (SCENARIO[5], SCENARIO[5]):
        with pytest.raises(DurabilityUnavailable):
            j.append(e)                                              # each retry first retries the roll, which fails
    assert j.read() == tuple(SCENARIO[:5])
    j._fs = fs                                                       # the store is writable again
    assert j.append(SCENARIO[5]) is Admission.APPLY and not j.needs_roll
    assert j.append(SCENARIO[5]) is Admission.ALREADY_APPLIED
    assert [j.append(e) for e in SCENARIO[6:]] == [Admission.APPLY] * (len(SCENARIO) - 6)
    j.close()
    r = recover(fs)
    assert r.verdict is Verdict.CLEAN and r.journal.read() == tuple(SCENARIO)
    d = durability_hold()
    assert (d.outcome, d.mode, d.hold_kind, d.reason) == (StoreOutcome.HARD_HOLD, EntriesMode.HOLD,
                                                          HoldKind.DURABILITY_UNAVAILABLE,
                                                          ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE)


@pytest.mark.parametrize('pending', PENDING)
@pytest.mark.parametrize('op_name', ['write', 'fsync'])
def test_after_a_failed_append_a_restart_finds_the_event_absent_or_complete_never_more(op_name, pending):
    fs, j = _journal_with(5)
    j._fs = FaultFs(fs, fail=_fail_on(op_name, ENOSPC))
    with pytest.raises(DurabilityUnavailable):
        j.append(SCENARIO[5])
    after = fs.crash('ntfs', pending)
    r = recover(after)
    assert r.verdict in (Verdict.CLEAN, Verdict.REPAIRED)
    got = r.journal.read()
    assert got in (tuple(SCENARIO[:5]), tuple(SCENARIO[:6]))
    if op_name == 'write':
        assert got == tuple(SCENARIO[:5])                            # the write itself failed: nothing reached the file
    adm = [r.journal.append(e) for e in SCENARIO[5:]]
    assert adm[0] in (Admission.APPLY, Admission.ALREADY_APPLIED) and r.journal.read() == tuple(SCENARIO)


class _HalfWriteFs(FaultFs):
    """The device takes half of the frame, then reports ENOSPC."""

    def write(self, h, data):
        self.inner.write(h, data[:len(data) // 2])
        raise oserror(ENOSPC)


def test_a_partially_written_failed_append_is_a_torn_tail_on_restart():
    fs, j = _journal_with(5)
    j._fs = _HalfWriteFs(fs)
    with pytest.raises(DurabilityUnavailable):
        j.append(SCENARIO[5])
    r = recover(fs.crash('ntfs', 'all'))
    assert r.verdict is Verdict.REPAIRED and r.journal.read() == tuple(SCENARIO[:5]) and len(r.evidence) >= 1


class _FailOnceFs(FaultFs):
    def __init__(self, inner, op, partial=False):
        super().__init__(inner)
        self.op, self.partial, self.armed = op, partial, True

    def write(self, h, data):
        if self.armed and self.op == 'write':
            self.armed = False
            if self.partial:
                self.inner.write(h, data[:len(data) // 2])
            raise oserror(ENOSPC)
        return super().write(h, data)

    def fsync(self, h):
        if self.armed and self.op == 'fsync':
            self.armed = False
            raise oserror(ENOSPC)
        return super().fsync(h)


@pytest.mark.parametrize('pending', PENDING)
@pytest.mark.parametrize('model', ['ntfs', 'posix'])
@pytest.mark.parametrize('op,partial', [('write', False), ('write', True), ('fsync', False)])
def test_a_one_shot_failure_is_sealed_off_at_once_so_every_restart_equals_the_in_process_state(op, partial, model,
                                                                                               pending):
    fs, j = _journal_with(5)
    j._fs = _FailOnceFs(fs, op, partial)
    with pytest.raises(DurabilityUnavailable):
        j.append(SCENARIO[5])
    assert not j.needs_roll                                          # rolled (or reopened) inside the failed append
    landed = op == 'fsync' or partial
    assert j.segment_no == (2 if landed else 1)
    if landed:
        ev_ref, new_seg = j.rolls[-1]
        assert ev_ref.name.startswith('evidence/failed-seg-000001-') and new_seg == 'seg-000002.seg'
    r = recover(fs.crash(model, pending))                            # the process dies right after the failure
    assert r.verdict is Verdict.CLEAN and r.journal.read() == tuple(SCENARIO[:5])   # never the failed event
    assert j.append(SCENARIO[5]) is Admission.APPLY                  # the in-process retry lands exactly once
    j.close()
    r2 = recover(fs)
    assert r2.verdict is Verdict.CLEAN and r2.journal.read() == tuple(SCENARIO[:6])


@pytest.mark.parametrize('code', [ENOSPC, EROFS])
def test_torn_tail_on_an_unwritable_store_is_hard_hold_with_no_partial_writes(code):     # NF-34 analogue
    fs = mem_journal(SCENARIO[:5])
    fs.put(seg(1), fs.read_bytes(seg(1)) + b'\x5a\xb2\x0a')
    before = fs.snapshot()
    f = FaultFs(fs, fail=lambda op, p, i: oserror(code))
    r = recover(f)
    assert r.verdict is Verdict.DURABILITY_UNAVAILABLE and r.journal is None
    assert r.directive == durability_hold()
    assert r.state is not None and r.state.last_sequence == 5          # the fold is intact for the emergency set
    assert fs.snapshot() == before and r.evidence == () and r.created == ()


def test_clean_journal_on_a_read_only_store_is_hard_hold_at_boot():                     # NF-35 analogue
    fs = mem_journal(SCENARIO[:5])
    before = fs.snapshot()
    r = recover(FaultFs(fs, fail=lambda op, p, i: oserror(EROFS)))
    assert r.verdict is Verdict.DURABILITY_UNAVAILABLE and r.directive.outcome is StoreOutcome.HARD_HOLD
    assert r.state.last_sequence == 5 and fs.snapshot() == before


def test_create_on_an_unwritable_store_raises_durability_unavailable():
    fs = MemFs(os.path.dirname(ACCT_DIR))
    with pytest.raises(DurabilityUnavailable):
        create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=FaultFs(fs, fail=lambda op, p, i: oserror(ENOSPC)))
    assert fs.snapshot() == MemFs(os.path.dirname(ACCT_DIR)).snapshot()


# ------------------------------------------------------------------------------------------------ HOLD mapping
def test_verdict_to_directive_table():
    want = {Verdict.CLEAN: (StoreOutcome.PROCEED, None, None), Verdict.REPAIRED: (StoreOutcome.PROCEED, None, None),
            Verdict.DAMAGED: (StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_SCHEMA_INVALID),
            Verdict.UNREADABLE: (StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_STATE_UNREADABLE),
            Verdict.MISSING: (StoreOutcome.HOLD, HoldKind.NORMAL, ReasonCode.RECOVERY_STATE_MISSING),
            Verdict.ABORT_RO: (StoreOutcome.ABORT_RO, None, ReasonCode.RECOVERY_SCHEMA_FUTURE),
            Verdict.DURABILITY_UNAVAILABLE: (StoreOutcome.HARD_HOLD, HoldKind.DURABILITY_UNAVAILABLE,
                                             ReasonCode.RECOVERY_DURABILITY_UNAVAILABLE)}
    assert set(want) == set(Verdict)
    for v, (o, k, r) in want.items():
        d = directive_for(v)
        assert (d.outcome, d.hold_kind, d.reason) == (o, k, r)
        assert d.account_context == (v is not Verdict.ABORT_RO)


ALLOWED = {(Purpose.PROTECT, Op.PLACE), (Purpose.ENTRY, Op.CANCEL), (Purpose.ADD, Op.CANCEL),
           (Purpose.ENTRY, Op.ADOPT), (Purpose.ADD, Op.ADOPT)} | {(u, Op.QUERY) for u in Purpose}


@pytest.mark.parametrize('purpose', list(Purpose))
@pytest.mark.parametrize('op', list(Op))
def test_hard_hold_permits_exactly_the_a23_a24_emergency_set(purpose, op):
    got = hard_hold_permits(purpose, op)
    assert got is (Permission.ALLOWED if (purpose, op) in ALLOWED else Permission.FORBIDDEN)


def test_the_hard_hold_mode_change_is_a_valid_nc01_record_and_journals_once_writable():
    fs, j = _journal_with(5)
    d = durability_hold()
    m = hold_event(6, T0 + 50)
    assert (m.to_mode, m.to_hold, m.reason) == (d.mode, d.hold_kind, d.reason)
    assert j.append(m) is Admission.APPLY
    st = j.state()
    assert (st.mode, st.hold_kind) == (EntriesMode.HOLD, HoldKind.DURABILITY_UNAVAILABLE)
    j.close()
    assert recover(fs).state.hold_kind is HoldKind.DURABILITY_UNAVAILABLE


# ------------------------------------------------------------------------------------------------ lost answer
def test_lost_answer_recovers_as_unknown_needing_a_venue_query_never_filled_or_never_filled():
    head, nf, fin, closed = lost_answer_events()
    entry = INTENTS['entry'].intent_id
    fs = mem_journal(head).crash('posix', 'drop')                  # sent durably; the answer died with the process
    r = recover(fs)
    v = r.state.intent(entry)
    assert v.recovery is IntentRecovery.UNKNOWN_NEEDS_QUERY and r.state.needs_venue_query == (entry,)
    assert v.state is IntentState.SUBMITTED and v.sent_at_ms is not None
    assert v.final_result is None and v.last_result is None and r.state.booked == ()
    j = r.journal
    assert j.append(nf) is Admission.APPLY                         # the query answers a bare not-found
    v = j.state().intent(entry)
    assert v.recovery is IntentRecovery.UNKNOWN_NEEDS_QUERY and v.final_result is None
    assert v.last_result.lookup is not None and j.state().booked == ()   # proves nothing, books nothing
    j.close()
    r = recover(fs)                                                # restart again: still UNKNOWN
    assert r.state.intent(entry).recovery is IntentRecovery.UNKNOWN_NEEDS_QUERY and r.state.booked == ()
    assert r.journal.append(fin) is Admission.APPLY                # the final exchange record arrives
    st = r.journal.state()
    assert st.intent(entry).recovery is IntentRecovery.FINAL_NOT_CLOSED
    assert st.booked == ((('SOLUSDT', 'LONG'), D('5')),)
    r.journal.close()
    r = recover(fs)                                                # crash between result and close: still final
    assert r.state.intent(entry).recovery is IntentRecovery.FINAL_NOT_CLOSED
    assert r.journal.append(closed) is Admission.APPLY and r.journal.state().intents == ()


def test_durable_but_never_sent_intent_recovers_as_durable_not_sent_and_can_end_not_sent():
    fs = mem_journal(SCENARIO[:2])
    r = recover(fs.crash('ntfs', 'drop'))
    entry = INTENTS['entry']
    v = r.state.intent(entry.intent_id)
    assert v.recovery is IntentRecovery.DURABLE_NOT_SENT and v.sent_at_ms is None
    assert r.state.durable_not_sent == (entry.intent_id,) and r.state.needs_venue_query == ()
    not_sent = ev(ResultObserved, 3, T0 + 5, reason=entry.reason,
                  result=result(entry, 0, T0 + 5, phase=ResultPhase.FINAL, evidence=Evidence.NOT_SENT,
                                executed_qty=D(0)))
    j = r.journal
    assert j.append(not_sent) is Admission.APPLY
    assert j.append(state(4, T0 + 5, entry, IntentState.DURABLE, IntentState.NOT_SENT)) is Admission.APPLY
    assert j.state().intents == () and j.state().booked == ()
