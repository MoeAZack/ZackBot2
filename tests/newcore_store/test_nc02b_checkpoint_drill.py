"""Checkpoint crash drill (runner wiring, ruling item 6): kill at every write boundary of a MANAGE session that
journals events and checkpoints the runner's portfolio, then boot.

Session (through a counting FaultFs, on a MANAGED store owning a lot): boot -> append 3 events -> checkpoint (JOURNAL
proof through 3) -> append 3 more -> checkpoint (through 6). For every mutating call, crash before / after it under
four power-loss models, then:
  - boot WITH the runner's fold hook: MANAGE, and the store covers the whole journal afterwards (a tail the last durable
    checkpoint missed is folded, matched and checkpointed at once); no acknowledged event or checkpoint is lost;
  - boot WITHOUT the hook (separate copy of the crashed disk): MANAGE exactly when the durable generation already covers
    the journal, else HOLD with journal_tail_unapplied - never MANAGE on an uncovered tail;
  - the only other outcome is HOLD on journal damage, and only under the NUL-fill model (Codex tail ruling).
"""
import dataclasses
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import FaultFs, SimulatedCrash
from nc02b_helpers import CIPHER, MEM_BASE, T, FakeExchange, account, mem, owned
from newcore.domain import OwnershipProof, ProofKind
from newcore.store import Outcome, open_store

ACC = account(acct=ACCOUNT_ID)
DEC = 'dec_' + '4' * 32
MODELS = [('ntfs', 'drop'), ('ntfs', 'half'), ('posix', 'all'), ('posix', 'zero')]


def lot_pf(through=None):
    pf, pos, orders = owned(acct=ACCOUNT_ID)
    pf = dataclasses.replace(pf, portfolio_id=AGGREGATE_ID)
    if through:
        pf = dataclasses.replace(pf, proof=OwnershipProof(kind=ProofKind.JOURNAL, at_ms=T, reconciliation_id=None,
                                                          key_digest=None, decision_id=None, through_sequence=through))
    return pf, FakeExchange(pos, orders)


def op(fs, ex, **kw):
    return open_store(MEM_BASE, ACC, exchange=ex, now_ms=T, fs=fs, cipher=CIPHER, aggregate_id=AGGREGATE_ID, **kw)


def base():
    fs = mem()
    pf, ex = lot_pf()
    b = op(fs, ex)
    b.store.promote(ex, T, candidate=pf, owner_decision_id=DEC)
    b.journal.close()
    return fs


def fold(journal, snapshot_pf):
    n = journal.last_sequence()
    return lot_pf(through=n)[0] if n else snapshot_pf


def drive(f, acked):
    """acked: ('event', seq) / ('ckpt', lsn) for every call that returned."""
    pf, ex = lot_pf()
    try:
        b = op(f, ex)
        assert b.outcome is Outcome.MANAGE, b.items
        for e in SCENARIO[:3]:
            b.journal.append(e)
            acked.append(('event', e.sequence))
        b.store.checkpoint(lot_pf(through=3)[0], T + 1)
        acked.append(('ckpt', 3))
        for e in SCENARIO[3:6]:
            b.journal.append(e)
            acked.append(('event', e.sequence))
        b.store.checkpoint(lot_pf(through=6)[0], T + 2)
        acked.append(('ckpt', 6))
        b.journal.close()
    except SimulatedCrash:
        return True
    return False


def op_count():
    f = FaultFs(base())
    assert not drive(f, [])
    return f.n


N = op_count()
POINTS = [(i, w) for i in range(N) for w in ('before', 'after')]


@pytest.mark.parametrize('i,when', POINTS, ids=[f'{i:03d}-{w}' for i, w in POINTS])
def test_checkpoint_crash_at_every_boundary(i, when):
    pf, ex = lot_pf()
    for model, pending in MODELS:
        fs0 = base()
        acked = []
        f = FaultFs(fs0, crash_at=i, when=when)
        assert drive(f, acked)
        ctx = (i, when, model, pending, f.trace[i], acked)
        last_event = max((s for k, s in acked if k == 'event'), default=0)
        last_ckpt = max((s for k, s in acked if k == 'ckpt'), default=0)

        plain_fs = fs0.crash(model, pending)
        plain = op(plain_fs, ex)
        hooked = op(fs0.crash(model, pending), ex, fold=fold)
        if hooked.outcome is Outcome.HOLD and any(it.cause == 'damage' for it in hooked.items):
            assert pending == 'zero', ctx                                    # a NUL-filled last frame: fail closed
            continue
        assert hooked.outcome is Outcome.MANAGE, (ctx, hooked.items)
        st = hooked.store
        assert st.journal.last_sequence() >= last_event, ctx                 # no acknowledged event lost
        assert st.current.lsn_upto == st.journal.last_sequence(), ctx        # the whole journal is covered now
        assert st.current.lsn_upto >= last_ckpt, ctx                         # no acknowledged checkpoint lost
        hooked.journal.close()

        if plain.outcome is Outcome.MANAGE:
            assert plain.store.current.lsn_upto == plain.store.journal.last_sequence(), ctx
        else:
            assert plain.outcome is Outcome.HOLD, ctx
            assert any(it.cause in ('journal_tail_unapplied', 'damage') for it in plain.items), (ctx, plain.items)
        if plain.journal is not None:
            plain.journal.close()


def test_the_drill_covers_event_appends_and_checkpoint_commits():
    f = FaultFs(base())
    drive(f, [])
    ops = [(op_, n) for _, op_, n in f.trace]
    assert sum(1 for o, n in ops if o == 'open_new' and n.endswith('.snap')) == 2
    assert sum(1 for o, n in ops if o == 'write' and n == 'seg-000001.seg') == 6
    assert any(n.startswith('HEAD') for o, n in ops if o in ('open_slot', 'open_new'))
    assert os.sep
