"""NC-02b crash drill (crash_matrix.md 1.2, 1.3, 1.5, 1.6, 1.8): kill at every mutating call of the store unit.

Scenario (one MemFs, every write through a counting FaultFs): INIT against a non-flat exchange (dirs, evidence DACL,
journal, G1 snapshot, HEAD, anchor, by-binding) -> owner promotion (G2) -> checkpoint (G3) -> a boot that finds the
exchange changed (HOLD generation G4) -> promotion back (G5). For every call index, crash before / after it under four
power-loss models, then boot again with the matching exchange and require:
  - no exception, never ABORT_RO, never DAMAGED-by-the-store-itself (a torn HEAD / anchor slot falls back);
  - the outcome follows the durable HEAD: MANAGED -> MANAGE, HOLD -> HOLD, HOLD_INIT -> HOLD-INIT, no HEAD -> a re-run
    INIT (HOLD-INIT) - so MANAGED exists only in a committed generation (no half-promotion, C-R4 / C-S6);
  - no acknowledged commit is lost (the durable HEAD generation >= the last acknowledged one);
  - generations stay monotonic: the next commit gets a generation above every snap/ name, orphans included (C-S5);
  - MANAGE is reached again after the drill (checkpoint + restart), HOLD only through promote().
"""
import os

import pytest

from nc02a_memfs import FaultFs, SimulatedCrash
from nc02b_helpers import ACCT, CIPHER, MEM_BASE, T, FakeExchange, account, mem, owned
from newcore.store.frame import KIND_HEAD
from newcore.store.records import SNAP_NAME_RE, Reader
from newcore.store.slots import PairState, read_pair
from newcore.store.snapfile import SnapDamage, decode_snapshot
from newcore.store.store import Mode, Paths, boot

P = Paths(MEM_BASE, ACCT)
DEC = 'dec_' + '5' * 32
MODELS = [('ntfs', 'drop'), ('ntfs', 'half'), ('posix', 'all'), ('posix', 'zero')]


def drive(f, acked):
    """Run the scenario through seam f. `acked` records the generation of every commit that returned."""
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    try:
        r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=CIPHER)
        st = r.store
        acked.append(st.current.generation)
        st.promote(ex, T, candidate=pf, owner_decision_id=DEC)
        acked.append(st.current.generation)
        st.checkpoint(st.current.portfolio, T + 1)
        acked.append(st.current.generation)
        st.journal.close()
        r2 = boot(MEM_BASE, account(), exchange=FakeExchange([], []), now_ms=T + 2, fs=f, cipher=CIPHER)
        acked.append(r2.store.current.generation)
        r2.store.promote(ex, T + 3)
        acked.append(r2.store.current.generation)
        r2.store.journal.close()
    except SimulatedCrash:
        return True
    return False


def op_count():
    f = FaultFs(mem())
    assert not drive(f, [])
    return f.n


N = op_count()
POINTS = [(i, w) for i in range(N) for w in ('before', 'after')]


def durable_head(fs):
    try:
        pair = read_pair(fs, P.account, 'HEAD', KIND_HEAD)
    except OSError:
        return None, None
    if pair.state is not PairState.OK:
        return pair.state, None
    try:
        raw = fs.read_bytes(os.path.join(P.snap, pair.doc['snapshot']['name']))
        sf = decode_snapshot(raw, Reader(), account_id=ACCT)
    except (OSError, SnapDamage):
        return PairState.OK, None
    return PairState.OK, sf


@pytest.mark.parametrize('i,when', POINTS, ids=[f'{i:03d}-{w}' for i, w in POINTS])
def test_store_crash_at_every_boundary(i, when):
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    for model, pending in MODELS:
        fs0 = mem()
        acked = []
        f = FaultFs(fs0, crash_at=i, when=when)
        assert drive(f, acked)
        fs = fs0.crash(model, pending)
        ctx = (i, when, model, pending, f.trace[i], acked)
        state, sf = durable_head(fs)
        r = boot(MEM_BASE, account(), exchange=ex, now_ms=T + 10, fs=fs, cipher=CIPHER)
        assert r.mode is not Mode.ABORT_RO, ctx
        if sf is not None:
            if acked:
                assert sf.generation >= acked[-1], ctx                                  # no acknowledged commit lost
            want = {'managed': Mode.MANAGE, 'hold': Mode.HOLD, 'hold_init': Mode.HOLD_INIT}[sf.provenance['trust']]
            assert r.mode is want, (ctx, r.mode, r.items, r.findings)
        else:
            assert not acked or state is not PairState.OK, ctx
            assert r.mode in (Mode.HOLD_INIT, Mode.HOLD), (ctx, r.mode, r.items)
        if r.mode is Mode.MANAGE:
            assert r.store.current.provenance['trust'] == 'managed'                    # no half-promotion
        st = r.store
        if st is None:
            continue
        names = [int(m.group(1)) for m in map(SNAP_NAME_RE.fullmatch, fs.listdir(P.snap)) if m] \
            if fs.kind(P.snap) == 'dir' else []
        if r.mode is Mode.MANAGE:
            st.checkpoint(st.current.portfolio, T + 11)
        elif r.mode is Mode.HOLD and r.candidate is not None and r.hold_kind.value == 'normal':
            assert st.promote(ex, T + 10).mode is Mode.MANAGE, ctx
        elif r.mode is Mode.HOLD_INIT:
            assert st.promote(ex, T + 10, candidate=pf, owner_decision_id=DEC).mode is Mode.MANAGE, ctx
        if st.current is not None and names:
            assert st.current.generation > max(names) or st.current.generation == max(names) and r.mode is \
                Mode.MANAGE and st.current.generation == max(names), ctx                # monotonic, never reused
        if st.journal is not None:
            st.journal.close()
        again = boot(MEM_BASE, account(), exchange=ex, now_ms=T + 20, fs=fs, cipher=CIPHER)
        assert again.mode in (Mode.MANAGE, Mode.HOLD, Mode.HOLD_INIT), ctx
        if again.store is not None and again.store.journal is not None:
            again.store.journal.close()


def test_the_drill_covers_snapshot_head_anchor_evidence_and_journal_writes():
    f = FaultFs(mem())
    drive(f, [])
    files = {name for _, op, name in f.trace if op in ('open_new', 'open_slot')}
    assert any(n.endswith('.snap') for n in files) and {'HEAD.a', 'HEAD.b'} <= files
    assert any(n.startswith(ACCT) for n in files) and 'seg-000001.seg' in files
    assert 'set_private_acl' in {op for _, op, _ in f.trace}
