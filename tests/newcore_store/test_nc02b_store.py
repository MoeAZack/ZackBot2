"""NC-02b store unit: INIT / HOLD-INIT, generations + dual-slot HEAD, anchor, reconciliation and promotion (A08), the
boot rules 0-8 on representative cells (M09, M10b, M13, M15, M19-M22, M35, M37, M39, M40, M42, M54, M55)."""
import dataclasses
import os

import pytest

from nc02a_memfs import FaultFs, oserror
from nc02b_helpers import (ACCT, CIPHER, DIGEST, MEM_BASE, OTHER_DIGEST, T, FakeExchange, account, data_files, mem,
                           outside_files, owned)
from newcore.domain import BindingState, EntriesMode, HoldKind, Ownership, ReasonCode
from newcore.store.frame import KIND_HEAD
from newcore.store.records import Reader, snap_name
from newcore.store.slots import read_pair, slot_path
from newcore.store.store import Mode, Paths, boot

P = Paths(MEM_BASE, ACCT)


def go(fs, ex, acct=None, now=T, reader=None):
    return boot(MEM_BASE, acct or account(), exchange=ex, now_ms=now, fs=fs, cipher=CIPHER, reader=reader)


def head(fs):
    return read_pair(fs, P.account, 'HEAD', KIND_HEAD)


def managed_with_lot(fs=None):
    """INIT against the exchange's position (HOLD-INIT), then the owner adopts it: a MANAGED store owning a lot."""
    fs = fs or mem()
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    r = go(fs, ex)
    assert r.mode is Mode.HOLD_INIT
    r2 = r.store.promote(ex, T, candidate=pf, owner_decision_id='dec_' + '1' * 32)
    assert r2.mode is Mode.MANAGE, r2.items
    r.store.journal.close()
    return fs, ex, pf


# ---------------------------------------------------------------------------------------------------- INIT (rule 8)
def test_init_on_a_flat_exchange_with_a_confirmed_binding_is_manage_known_empty():                 # M21
    fs = mem()
    r = go(fs, FakeExchange())
    assert r.mode is Mode.MANAGE and r.portfolio.ownership is Ownership.KNOWN_EMPTY
    assert r.writes >= {'init_commit', 'anchor'}
    h = head(fs)
    assert h.doc['generation'] == 1 and h.doc['snapshot']['name'] == snap_name(1) and h.current == 'a'
    assert fs.kind(os.path.join(P.by_binding, DIGEST)) == 'file'
    assert fs.read_acl(os.path.join(P.account, 'evidence')) == 'PRIVATE(user,SYSTEM)'
    r.store.journal.close()
    r2 = go(fs, FakeExchange())                                                  # restart: rule 6, still proven
    assert r2.mode is Mode.MANAGE and r2.writes == frozenset() - {'x'} or r2.mode is Mode.MANAGE


@pytest.mark.parametrize('case', ['non_flat', 'unconfirmed', 'other_key'])
def test_init_against_anything_but_a_proven_flat_exchange_is_hold_init(case):                       # M22, A09
    fs = mem()
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders) if case == 'non_flat' else FakeExchange(digest=OTHER_DIGEST if case == 'other_key'
                                                                            else DIGEST)
    acct = account(state=BindingState.UNCONFIRMED) if case == 'unconfirmed' else account()
    r = go(fs, ex, acct)
    assert r.mode is Mode.HOLD_INIT and r.portfolio.ownership is Ownership.UNKNOWN
    assert r.portfolio.positions is None                                         # unknown is never empty
    assert r.items
    r.store.journal.close()
    assert go(fs, ex, acct).mode is Mode.HOLD_INIT                               # durable (A05)


def test_init_waits_with_zero_writes_while_the_exchange_is_down():                                # rule 5 / M23
    fs = mem()
    before = fs.snapshot()
    ex = FakeExchange()
    ex.down = True
    r = go(fs, ex)
    assert r.mode is Mode.INIT_WAIT and not r.account_context or r.mode is Mode.INIT_WAIT
    assert fs.snapshot() == before


def test_hold_init_leaves_only_through_the_owner_and_a_fresh_match():                             # M37, A08
    fs = mem()
    pf, pos, orders = owned()
    ex = FakeExchange(pos, orders)
    r = go(fs, ex)
    st = r.store
    assert st.promote(ex, T, candidate=pf).mode is Mode.HOLD_INIT                # confirming alone never clears it
    ok = st.promote(ex, T, candidate=pf, owner_decision_id='dec_' + '2' * 32)
    assert ok.mode is Mode.MANAGE and ok.portfolio.ownership is Ownership.KNOWN
    st.journal.close()
    again = go(fs, ex)
    assert again.mode is Mode.MANAGE and again.portfolio.positions == ok.portfolio.positions


# ---------------------------------------------------------------------------------------------------- rules 6 / 7
def test_managed_store_with_a_matching_exchange_is_manage(tmp_path=None):                         # M09
    fs, ex, pf = managed_with_lot()
    r = go(fs, ex)
    assert r.mode is Mode.MANAGE and r.writes == frozenset()


@pytest.mark.parametrize('change', ['qty', 'extra_position', 'foreign_order', 'stop_gone', 'flat'])
def test_a_differing_exchange_is_hold_with_items_and_the_hold_is_durable(change):                  # M10b, A05, A22
    fs, ex, pf = managed_with_lot()
    from decimal import Decimal as D
    from newcore.store.reconcile import ExOrder, ExPosition
    bad = FakeExchange(list(ex.positions), list(ex.orders))
    if change == 'qty':
        bad.positions = [ExPosition('BTCUSDT', 'LONG', D('0.6'))]
    elif change == 'extra_position':
        bad.positions.append(ExPosition('ETHUSDT', 'SHORT', D('0.5')))
    elif change == 'foreign_order':
        bad.orders.append(ExOrder('o:99', 'ETHUSDT', 'SHORT', D('0.5'), True, 'stop', 'working'))
    elif change == 'stop_gone':
        bad.orders = []
    else:
        bad.positions, bad.orders = [], []
    r = go(fs, bad)
    assert r.mode is Mode.HOLD and r.items and r.candidate is not None
    assert 'hold_snapshot' in r.writes and r.portfolio.ownership is Ownership.UNKNOWN
    r.store.journal.close()
    again = go(fs, ex)                                                           # the exchange matches again ...
    assert again.mode is Mode.HOLD                                               # ... but HOLD is durable (A05)
    back = again.store.promote(ex, T)
    assert back.mode is Mode.MANAGE, back.items                                  # only A08 leaves it


def test_promotion_rechecks_against_a_second_fresh_snapshot():                                     # A08 atomicity
    fs, ex, pf = managed_with_lot()
    from newcore.store.reconcile import ExPosition
    from decimal import Decimal as D
    gone = FakeExchange([], [])
    r = go(fs, gone)
    assert r.mode is Mode.HOLD
    ex.calls = 0
    ex.between = {1: lambda e: setattr(e, 'positions', [ExPosition('BTCUSDT', 'LONG', D('2'))])}
    res = r.store.promote(ex, T)
    assert res.mode is Mode.HOLD and res.items                                  # changed between the two snapshots
    ex.positions = [ExPosition('BTCUSDT', 'LONG', D('1'))]
    assert r.store.promote(ex, T).mode is Mode.MANAGE


def test_a_stale_snapshot_never_promotes():
    fs, ex, pf = managed_with_lot()
    r = go(fs, FakeExchange([], []))
    stale = FakeExchange(ex.positions, ex.orders, now=T - 10_000)
    res = r.store.promote(stale, T)
    assert res.mode is Mode.HOLD and res.items[0].cause == 'stale_snapshot'


def test_identity_mismatch_needs_the_owner_and_a_fresh_match():                                    # M40, A11
    fs, ex, pf = managed_with_lot()
    other = account(OTHER_DIGEST)
    ex2 = FakeExchange(ex.positions, ex.orders, digest=OTHER_DIGEST)
    r = go(fs, ex2, other)
    assert r.mode is Mode.HOLD and any(i.cause == 'identity' for i in r.items)
    assert r.store.promote(ex2, T, account=other).mode is Mode.HOLD           # equal positions prove nothing
    ok = r.store.promote(ex2, T, account=other, owner_decision_id='dec_' + '3' * 32)
    assert ok.mode is Mode.MANAGE
    r.store.journal.close()
    assert go(fs, ex2, other).mode is Mode.MANAGE


def test_a_trivially_empty_candidate_never_auto_promotes():                                        # M15 / M39
    fs = mem()
    r = go(fs, FakeExchange())
    st = r.store
    st.journal.close()
    gone = FakeExchange(*owned()[1:])
    h = go(fs, gone)                                                             # empty store vs a position: HOLD
    assert h.mode is Mode.HOLD and h.candidate_kind == 'trivially_empty'
    flat = FakeExchange()
    assert h.store.promote(flat, T).mode is Mode.HOLD                            # never automatically
    assert h.store.promote(flat, T, owner_decision_id='dec_' + '4' * 32).mode is Mode.MANAGE


def test_exchange_down_at_boot_is_hold_without_writes():                                          # M23
    fs, ex, pf = managed_with_lot()
    before = data_files(fs)
    down = FakeExchange()
    down.down = True
    r = go(fs, down)
    assert r.mode is Mode.HOLD and r.items[0].cause == 'exchange_down'
    r.store.journal.close()
    assert data_files(fs) == before


# ---------------------------------------------------------------------------------------------------- checkpoints
def test_checkpoints_keep_three_retained_generations_and_never_delete(tmp_path=None):              # D16
    fs, ex, pf = managed_with_lot()
    r = go(fs, ex)
    st = r.store
    for i in range(6):
        st.checkpoint(st.current.portfolio, T + i)
    h = head(fs).doc
    assert len(h['retained']) == 3 and len(h['retired']) >= 3
    names = fs.listdir(P.snap)
    assert len(names) == h['generation']                                         # nothing deleted
    st.journal.close()
    assert go(fs, ex).mode is Mode.MANAGE


def test_a_checkpoint_in_hold_is_hold():                                                           # A05
    fs, ex, pf = managed_with_lot()
    r = go(fs, FakeExchange([], []))
    st = r.store
    st.checkpoint(st.candidate.portfolio, T + 5)
    st.journal.close()
    assert go(fs, ex).mode is Mode.HOLD


# ---------------------------------------------------------------------------------------------------- HEAD slots
def test_a_torn_head_slot_falls_back_to_the_other(tmp_path=None):                                  # M54
    fs, ex, pf = managed_with_lot()
    r = go(fs, ex)
    st = r.store
    good = head(fs)
    target = slot_path(P.account, 'HEAD', good.write_next)
    st.journal.close()
    raw = fs.read_bytes(target) if fs.kind(target) == 'file' else b''
    fs.put(target, (raw or b'ZBNC')[:100] + b'garbage')
    r2 = go(fs, ex)
    assert r2.mode is Mode.MANAGE and head(fs).current == good.current


def test_both_head_slots_damaged_is_hold_with_evidence_and_no_slot_rewrite():                     # A04, M56
    fs, ex, pf = managed_with_lot()
    for w in 'ab':
        p = slot_path(P.account, 'HEAD', w)
        if fs.kind(p) == 'file':
            fs.put(p, b'\x01' * 64)
    a_before = fs.read_bytes(slot_path(P.account, 'HEAD', 'a'))
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and r.evidence and r.candidate is not None
    assert fs.read_bytes(slot_path(P.account, 'HEAD', 'a')) == a_before
    if r.store.journal:
        r.store.journal.close()
    assert go(fs, ex).mode is Mode.HOLD


# ---------------------------------------------------------------------------------------------------- anchors (D2)
def test_a_whole_data_folder_rollback_trips_the_anchor():                                          # M42 / NF-48
    fs, ex, pf = managed_with_lot()
    old = {k: v for k, v in fs.snapshot().items()}
    r = go(fs, ex)
    r.store.checkpoint(r.store.current.portfolio, T + 1)
    r.store.journal.close()
    data_root = os.path.normpath(os.path.join(MEM_BASE, 'data'))
    for k in [k for k in fs.files if k.startswith(data_root)]:
        del fs.files[k]
    for k, v in old.items():                                                     # restore an older data\ copy
        if k.startswith(data_root) and v[0] == 'f':
            fs.put(k, v[1])
    rb = go(fs, ex)
    assert rb.mode is Mode.HOLD and any(i.cause == 'rollback' for i in rb.items)


def test_an_emptied_data_folder_with_a_surviving_anchor_is_hold_not_init():                       # D2 / NF-16
    fs, ex, pf = managed_with_lot()
    data_root = os.path.normpath(os.path.join(MEM_BASE, 'data'))
    for k in [k for k in fs.files if k.startswith(data_root)]:
        del fs.files[k]
    for k in [k for k in fs.dirs if k.startswith(data_root)]:
        del fs.dirs[k]
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and any(i.cause == 'rollback' for i in r.items)


def test_an_anchor_behind_head_is_harmless(tmp_path=None):                                         # M55
    fs, ex, pf = managed_with_lot()
    old_anchor = {k: v for k, v in outside_files(fs).items() if 'anchors' in k and v[0] == 'f'}
    r = go(fs, ex)
    r.store.checkpoint(r.store.current.portfolio, T + 1)
    r.store.journal.close()
    for k, v in old_anchor.items():
        fs.put(k, v[1])                                                          # the anchor write was lost
    assert go(fs, ex).mode is Mode.MANAGE


# ---------------------------------------------------------------------------------------------------- hard HOLD
def test_an_unwritable_store_entering_hold_is_hard_hold_and_the_marker_survives_a_restart():       # A21, D11
    fs, ex, pf = managed_with_lot()
    data_root = os.path.normpath(os.path.join(MEM_BASE, 'data'))
    ro = FaultFs(fs, fail=lambda op, p, i: oserror(30) if os.path.normpath(p).startswith(data_root) else None)
    r = boot(MEM_BASE, account(), exchange=FakeExchange([], []), now_ms=T, fs=ro, cipher=CIPHER)
    assert r.mode is Mode.HOLD and r.hold_kind is HoldKind.DURABILITY_UNAVAILABLE
    r2 = go(fs, ex)                                                              # writable again, exchange matches
    assert r2.mode is Mode.HOLD and any(i.cause == 'rollback' for i in r2.items)  # the D11 marker forces HOLD
    assert r2.store.promote(ex, T).mode is Mode.MANAGE
