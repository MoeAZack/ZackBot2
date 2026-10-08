"""The read-only recovery view (S3 runner, hard HOLD at boot): when recover_journal answers DURABILITY_UNAVAILABLE it
still hands over the events up to the last verified record, the rebuilt gate and the folded state; it has no write
handle and refuses every append without touching the store."""
import pytest

from nc02a_events import ENTRY_DEC, ENTRY_KEY, INTENTS, SCENARIO
from nc02a_memfs import FaultFs, oserror
from nc02a_util import mem_journal, recover, seg
from newcore.ports.journal import JournalPort, JournalUnavailable, claim_signal
from newcore.store import DurabilityUnavailable, IntentRecovery, ReadOnlyJournal, StoreOutcome, Verdict

N = 8


def _unwritable(fs):
    return FaultFs(fs, fail=lambda op, p, i: oserror(30))


@pytest.mark.parametrize('torn', [False, True])
def test_durability_unavailable_hands_over_a_read_only_view(torn):
    fs = mem_journal(SCENARIO[:N])
    if torn:
        fs.put(seg(1), fs.read_bytes(seg(1)) + b'\x5a\xb2\x0a\x00\x40')       # a torn record after the last good one
    before = fs.snapshot()
    f = _unwritable(fs)
    r = recover(f)
    assert r.verdict is Verdict.DURABILITY_UNAVAILABLE and r.journal is None
    assert r.directive.outcome is StoreOutcome.HARD_HOLD
    v = r.view
    assert isinstance(v, ReadOnlyJournal) and isinstance(v, JournalPort) and v.readonly
    assert v.read() == tuple(SCENARIO[:N]) and v.read(5) == tuple(SCENARIO[5:N])     # up to the last verified record
    assert v.last_sequence() == N and v.find_decision(ENTRY_DEC) == SCENARIO[0]
    st = v.state()
    assert st.last_sequence == N and st.intent(INTENTS['stop'].intent_id).recovery is IntentRecovery.UNKNOWN_NEEDS_QUERY
    assert not claim_signal(v.gate().grammar, ENTRY_KEY).fresh                          # the gate is rebuilt
    calls = len(f.trace)
    for e in (SCENARIO[N], SCENARIO[0]):
        with pytest.raises(DurabilityUnavailable) as ex:
            v.append(e)
        assert isinstance(ex.value, JournalUnavailable)
    assert len(f.trace) == calls and fs.snapshot() == before                          # nothing written, ever
    assert v.read() == tuple(SCENARIO[:N])


def test_other_verdicts_carry_no_view():
    fs = mem_journal(SCENARIO[:N])
    r = recover(fs)
    assert r.verdict is Verdict.CLEAN and r.view is None
    fs.put(seg(1), fs.read_bytes(seg(1))[:200] + b'\x01' + fs.read_bytes(seg(1))[201:])
    assert recover(fs).verdict is Verdict.DAMAGED and recover(fs).view is None
