"""The runner-facing store API: open_store()'s typed outcomes and the checkpoint that lets a restart MANAGE without an
event -> portfolio apply in the store (ruling item 6)."""
import dataclasses
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import FaultFs, oserror
from nc02b_helpers import CIPHER, MEM_BASE, T, FakeExchange, account, mem, owned
from newcore.domain import EntriesMode, HoldKind, OwnershipProof, ProofKind, ReasonCode
from newcore.store import Outcome, open_store
from newcore.store.frame import KIND_HEAD
from newcore.store.slots import read_pair, write_slot
from newcore.store.store import Paths

ACC = account(acct=ACCOUNT_ID)
P = Paths(MEM_BASE, ACCOUNT_ID)
DEC = 'dec_' + '6' * 32


def lot_pf(through=None):
    pf, pos, orders = owned(acct=ACCOUNT_ID)
    pf = dataclasses.replace(pf, portfolio_id=AGGREGATE_ID)
    if through is not None:
        pf = dataclasses.replace(pf, proof=OwnershipProof(kind=ProofKind.JOURNAL, at_ms=T, reconciliation_id=None,
                                                          key_digest=None, decision_id=None, through_sequence=through))
    return pf, FakeExchange(pos, orders)


def op(fs, ex, **kw):
    return open_store(MEM_BASE, ACC, exchange=ex, now_ms=T, fs=fs, cipher=CIPHER, aggregate_id=AGGREGATE_ID, **kw)


def managed(fs=None):
    fs = fs or mem()
    pf, ex = lot_pf()
    b = op(fs, ex)
    assert b.outcome is Outcome.HOLD_INIT and b.journal is not None
    b.store.promote(ex, T, candidate=pf, owner_decision_id=DEC)
    b.journal.close()
    b2 = op(fs, ex)
    assert b2.outcome is Outcome.MANAGE, b2.items
    return fs, ex, b2


def close(b):
    if b.journal is not None:
        b.journal.close()


# ---------------------------------------------------------------------------------------------------- outcomes
def test_first_flat_run_is_init_then_manage():
    fs = mem()
    b = op(fs, FakeExchange())
    assert b.outcome is Outcome.INIT and b.may_open_risk and b.journal is not None and b.view is None
    close(b)
    b2 = op(fs, FakeExchange())
    assert b2.outcome is Outcome.MANAGE
    close(b2)


def test_hold_init_hold_and_promotion_outcomes():
    fs, ex, b = managed()
    close(b)
    h = op(fs, FakeExchange([], []))
    assert h.outcome is Outcome.HOLD and h.hold_kind is HoldKind.NORMAL and not h.may_open_risk
    assert h.candidate is not None and h.journal is not None                    # local journaling allowed in HOLD
    assert h.store.promote(ex, T).mode.value == 'manage'
    close(h)


def test_abort_ro_has_no_account_context():
    fs, ex, b = managed()
    close(b)
    pair = read_pair(fs, P.account, 'HEAD', KIND_HEAD)
    write_slot(fs, P.account, 'HEAD', pair.write_next, KIND_HEAD, {**pair.doc, 'commit_seq': 99,
                                                                    'min_reader_version': 9})
    a = op(fs, ex)
    assert a.outcome is Outcome.ABORT_RO and a.store is None and a.journal is None and a.view is None


@pytest.mark.parametrize('why', ['locked', 'exchange_down_first_run', 'unwritable_flat_first_run'])
def test_reject_means_not_started_and_nothing_written(why):
    fs = mem()
    if why == 'locked':
        fs, ex, b = managed()                                                    # b holds the journal: a 2nd process
        before = fs.snapshot()
        r = op(fs, ex)
        assert r.outcome is Outcome.REJECT and 'locked' in r.reason and fs.snapshot() == before
        close(b)
        return
    before = fs.snapshot()
    ex = FakeExchange()
    f = fs
    if why == 'exchange_down_first_run':
        ex.down = True
    else:
        data = os.path.normpath(os.path.join(MEM_BASE, 'data'))
        f = FaultFs(fs, fail=lambda o, p, i: oserror(30) if os.path.normpath(p).startswith(data) else None)
    r = open_store(MEM_BASE, ACC, exchange=ex, now_ms=T, fs=f, cipher=CIPHER, aggregate_id=AGGREGATE_ID)
    assert r.outcome is Outcome.REJECT and r.store is None and r.journal is None
    data_files = {k: v for k, v in fs.snapshot().items() if os.path.normpath(os.path.join(MEM_BASE, 'data')) in k}
    assert data_files == {k: v for k, v in before.items() if os.path.normpath(os.path.join(MEM_BASE, 'data')) in k}


def test_hard_hold_hands_over_a_read_only_view_with_the_events():
    fs, ex, b = managed()
    for e in SCENARIO[:4]:
        b.journal.append(e)
    close(b)
    data = os.path.normpath(os.path.join(MEM_BASE, 'data'))
    ro = FaultFs(fs, fail=lambda o, p, i: oserror(30) if os.path.normpath(p).startswith(data) else None)
    h = open_store(MEM_BASE, ACC, exchange=ex, now_ms=T, fs=ro, cipher=CIPHER, aggregate_id=AGGREGATE_ID)
    assert h.outcome is Outcome.HOLD and h.hard_hold and h.journal is None
    assert h.view is not None and h.view.read() == tuple(SCENARIO[:4]) and h.view.last_sequence() == 4
    with pytest.raises(Exception):
        h.view.append(SCENARIO[4])


# ---------------------------------------------------------------------------------------------------- checkpoint
def test_a_checkpoint_after_events_lets_the_restart_manage_without_any_apply():
    fs, ex, b = managed()
    st = b.store
    for e in SCENARIO[:5]:
        b.journal.append(e)
    assert st.checkpoint_due
    pf, _ = lot_pf(through=5)
    sf = st.checkpoint(pf, T + 1)
    assert sf.lsn_upto == 5 and not st.checkpoint_due and sf.provenance['trust'] == 'managed'
    close(b)
    r = op(fs, ex)                                                               # no fold hook needed
    assert r.outcome is Outcome.MANAGE and r.portfolio.proof.through_sequence == 5
    close(r)


def test_a_crash_between_an_event_and_its_checkpoint_needs_the_fold_hook():
    fs, ex, b = managed()
    for e in SCENARIO[:6]:
        b.journal.append(e)
    close(b)                                                                     # the checkpoint never happened
    plain = op(fs, ex)
    assert plain.outcome is Outcome.HOLD and any(i.cause == 'journal_tail_unapplied' for i in plain.items)
    close(plain)

    fs2, ex2, b2 = managed()
    for e in SCENARIO[:6]:
        b2.journal.append(e)
    close(b2)
    seen = []

    def fold(journal, snapshot_pf):
        seen.append((journal.last_sequence(), snapshot_pf.proof.kind))
        return lot_pf(through=journal.last_sequence())[0]
    r = op(fs2, ex2, fold=fold)
    assert r.outcome is Outcome.MANAGE and seen == [(6, ProofKind.RECONCILED)]
    assert r.store.current.lsn_upto == 6 and not r.store.checkpoint_due            # the tail was checkpointed at once
    close(r)
    again = op(fs2, ex2)                                                         # and later boots need no hook
    assert again.outcome is Outcome.MANAGE
    close(again)


@pytest.mark.parametrize('bad', ['stale_proof', 'other_aggregate', 'raises', 'none', 'no_match'])
def test_a_fold_that_is_not_a_proven_view_of_this_journal_holds(bad):
    fs, ex, b = managed()
    for e in SCENARIO[:6]:
        b.journal.append(e)
    close(b)

    def fold(journal, snapshot_pf):
        if bad == 'stale_proof':
            return lot_pf(through=journal.last_sequence() - 1)[0]
        if bad == 'other_aggregate':
            return dataclasses.replace(lot_pf(through=journal.last_sequence())[0], portfolio_id='pf_' + 'f' * 32)
        if bad == 'raises':
            raise ValueError('cannot fold')
        if bad == 'none':
            return None
        pf = lot_pf(through=journal.last_sequence())[0]
        return pf
    ex_used = FakeExchange([], []) if bad == 'no_match' else ex
    r = op(fs, ex_used, fold=fold)
    assert r.outcome is Outcome.HOLD and r.items


def test_checkpoint_guards():
    fs, ex, b = managed()
    st = b.store
    pf, _ = lot_pf()
    with pytest.raises(ValueError):
        st.checkpoint(dataclasses.replace(pf, portfolio_id='pf_' + 'f' * 32), T + 1)
    held = dataclasses.replace(st.current.portfolio, entries_mode=EntriesMode.HOLD, hold_kind=HoldKind.NORMAL,
                               pause_reasons=(ReasonCode.RECONCILE_UNRECONCILED,))
    sf = st.checkpoint(held, T + 2)                                              # a HOLD portfolio is a HOLD generation
    assert sf.provenance['trust'] == 'hold'
    close(b)
    assert op(fs, ex).outcome is Outcome.HOLD
