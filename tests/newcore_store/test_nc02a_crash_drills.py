"""Crash drills (nc02_design.md 10.2 / 10.3, crash_matrix.md 1.1 and 1.4): kill at EVERY mutating file-system call.

A record pass runs the scenario through a counting FaultFs; then, for every call index i, crash before and after it,
and materialise every power-loss outcome (directory entries kept / dropped x un-fsynced bytes dropped / kept / torn /
zero-filled). A fresh recovery must then show:
  - no lost durable event: every event whose append returned is there;
  - no phantom: at most the one in-flight event more, byte-identical, in order; nothing else;
  - written-but-never-sent intents are DURABLE_NOT_SENT; sent intents without a final result are UNKNOWN_NEEDS_QUERY
    (never assumed filled or never-filled, nothing booked); a durable FINAL result is FINAL_NOT_CLOSED - checked
    against an oracle computed from the scenario tags, not from the store;
  - never DAMAGED or ABORT_RO; a torn tail is copied to evidence and sealed, the sealed bytes never change;
  - the scenario then completes (an already-durable in-flight event re-appends as ALREADY_APPLIED), and the next
    recovery is CLEAN and writes nothing.
Drill A: create + the 18-event scenario. Drill B: recover a journal with a torn tail (evidence copy, seal, roll), then
append the rest (crash_matrix C-T1..C-T3, C-E1..C-E8).
"""
import os
from decimal import Decimal as D

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, INTENT_IDS, SCENARIO, expected_recovery
from nc02a_memfs import PENDING, FaultFs, MemFs, SimulatedCrash
from nc02a_util import ACCT_DIR, mem_journal, recover, seg
from newcore.domain import canonical_bytes
from newcore.ports.journal import Admission
from newcore.store import Verdict, create_journal
from newcore.store.frame import RT_EVENT, frame
from newcore.store.recovery import evidence_bytes

MODELS = ('ntfs', 'posix')
B_START = 6
B_TAIL = frame(RT_EVENT, canonical_bytes(SCENARIO[B_START]))[:37]


def base_a():
    return MemFs(os.path.dirname(ACCT_DIR))


def base_b():
    fs = mem_journal(SCENARIO[:B_START])
    fs.put(seg(1), fs.read_bytes(seg(1)) + B_TAIL)
    return fs


def drive_a(f):
    """Returns (acked, journal_created). Raises SimulatedCrash through."""
    state = {'acked': 0, 'created': False}
    try:
        j = create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=f)
        state['created'] = True
        for e in SCENARIO:
            j.append(e)
            state['acked'] += 1
    except SimulatedCrash:
        return state, True
    return state, False


def drive_b(f):
    state = {'acked': B_START, 'created': True}
    try:
        r = recover(f)
        assert r.verdict is Verdict.REPAIRED
        for e in SCENARIO[B_START:]:
            r.journal.append(e)
            state['acked'] += 1
    except SimulatedCrash:
        return state, True
    return state, False


DRILLS = {'A': (base_a, drive_a), 'B': (base_b, drive_b)}


def op_count(drill):
    base, drive = DRILLS[drill]
    f = FaultFs(base())
    _, crashed = drive(f)
    assert not crashed
    return f.n


POINTS = [(d, i, w) for d in DRILLS for i in range(op_count(d)) for w in ('before', 'after')]


def check_classification(st, k):
    oracle, booked = expected_recovery(k)
    live = {v.intent_id: v for v in st.intents}
    closed = dict(st.closed)
    for name, want in oracle.items():
        iid = INTENT_IDS[name]
        if want == 'closed':
            assert iid in closed and iid not in live, (name, k)
        else:
            v = live[iid]
            assert v.recovery.value == want, (name, k, v.recovery)
            if want == 'unknown_needs_query':
                assert v.final_result is None and v.sent_at_ms is not None, (name, k)
                assert v.state.value in ('submitted', 'working', 'unknown', 'cancelling'), (name, k, v.state)
            if want == 'durable_not_sent':
                assert v.sent_at_ms is None
    assert set(live) == {INTENT_IDS[n] for n, w in oracle.items() if w != 'closed'}, k
    assert dict(st.booked).get(('SOLUSDT', 'LONG'), D(0)) == booked, k


@pytest.mark.parametrize('drill,i,when', POINTS, ids=[f'{d}-{i:02d}-{w}' for d, i, w in POINTS])
def test_crash_at_every_write_and_fsync_boundary(drill, i, when):
    base, drive = DRILLS[drill]
    for model in MODELS:
        for pending in PENDING:
            fs0 = base()
            torn_seg1 = fs0.read_bytes(seg(1)) if drill == 'B' else None
            f = FaultFs(fs0, crash_at=i, when=when)
            st, crashed = drive(f)
            assert crashed
            fs = fs0.crash(model, pending)
            ctx = (drill, i, when, model, pending, f.trace[i])
            r = recover(fs)
            if not st['created']:
                assert r.verdict in (Verdict.MISSING, Verdict.REPAIRED, Verdict.CLEAN), ctx
                if r.verdict is Verdict.MISSING:
                    j = create_journal(ACCT_DIR, ACCOUNT_ID, AGGREGATE_ID, fs=fs)
                else:
                    j = r.journal
                    assert j.read() == (), ctx
            else:
                assert r.verdict in (Verdict.CLEAN, Verdict.REPAIRED), (ctx, r.verdict, r.findings)
                j = r.journal
            got = j.read()
            k = len(got)
            acked = st['acked']
            assert acked <= k <= acked + 1, (ctx, acked, k)                        # no loss, at most the in-flight one
            assert [canonical_bytes(e) for e in got] == [canonical_bytes(e) for e in SCENARIO[:k]], ctx   # no phantom
            check_classification(j.state(), k)
            if torn_seg1 is not None:
                assert fs.read_bytes(seg(1)) == torn_seg1, ctx                     # sealed bytes never change
            for ref in r.evidence:
                blob = fs.read_bytes(os.path.join(ACCT_DIR, *ref.name.split('/')))
                assert blob == evidence_bytes(ACCOUNT_ID, ref.segment, ref.offset, blob[-ref.size:]), ctx
            adm = [j.append(e) for e in SCENARIO[acked:]]
            assert adm == [Admission.ALREADY_APPLIED] * (k - acked) + [Admission.APPLY] * (len(SCENARIO) - k), ctx
            assert j.read() == tuple(SCENARIO)
            j.close()
            r2 = recover(fs)
            assert r2.verdict is Verdict.CLEAN and r2.journal.read() == tuple(SCENARIO), ctx
            assert r2.state.intents == () and dict(r2.state.booked) == {('SOLUSDT', 'LONG'): D(0)}
            r2.journal.close()
            before = fs.snapshot()
            g = FaultFs(fs)
            r3 = recover(g)
            assert r3.verdict is Verdict.CLEAN and [op for _, op, _ in g.trace] == ['open_append'], ctx
            r3.journal.close()
            assert fs.snapshot() == before


def test_the_drills_cover_every_boundary_kind():
    for drill, n in (('A', op_count('A')), ('B', op_count('B'))):
        f = FaultFs(DRILLS[drill][0]())
        DRILLS[drill][1](f)
        ops = {op for _, op, _ in f.trace}
        assert {'open_new', 'write', 'fsync', 'fsync_dir', 'open_append'} <= ops and f.n == n
    fb = FaultFs(base_b())
    drive_b(fb)
    assert [label for _, label in fb.marks][:3] == ['C-T0', 'C-T1', 'C-T3']
