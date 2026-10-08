"""Repros for Cowork's second pass (PR #43 round, nc-02b items), written before the fixes.

N3 no raw OSError ever leaves boot() / open_store(): a failing kind / listdir / read / stat / lock / write anywhere is a
   typed outcome (HOLD / HOLD-INIT / REJECT / ABORT_RO ...), never an exception.
N4 a stray file in snap/ (a name that is not a generation, g<20>.snap) is reported and ignored, like the journal/ stray
   rule: never DAMAGE, never deleted.
N7 the anchors/by-binding/<key digest> index is USED at boot: an entry naming another account, a malformed entry (not
   canonical, wrong shape, another digest inside) or an unreadable one blocks INIT / MANAGE as an identity HOLD; bind()
   never overwrites an entry of another account; an owner promotion onto a key bound to another account is refused
   before anything is committed.
N6 the envelope on the REAL file system with the portable test cipher runs on every OS (the DPAPI / DACL tests stay
   Windows-only with a skip reason).
"""
import errno
import os
import sys

import pytest

from nc02a_memfs import FaultFs, oserror
from nc02b_helpers import ACCT, CIPHER, DIGEST, MEM_BASE, OTHER_DIGEST, T, FakeExchange, account, data_files, mem, owned
from newcore.store import Outcome, open_store
from newcore.store.envelope import read_envelope, write_envelope
from newcore.store.fs import RealFs
from newcore.store.header import canonical_json
from newcore.store.store import Mode, Paths, boot
from test_nc02b_store import go, managed_with_lot

P = Paths(MEM_BASE, ACCT)
OTHER_ACCT = 'acct_' + 'c3' * 16
DEC = 'dec_' + '9' * 32


def _put(fs, p, data):
    for d in (P.anchors, P.by_binding):
        if fs.kind(d) == 'missing':
            fs.mkdir(d)
    h = fs.open_new(p)
    fs.write(h, data)
    fs.fsync(h)
    fs.close(h)


def _close(r):
    st = getattr(r, 'store', None)
    if st is not None and st.journal is not None:
        st.journal.close()


# ---------------------------------------------------------------------------------------------------- N3
def _sweep(make_fs, ex_factory, mode):
    """Fail the n-th read (or mutating) call of a boot; return [(n, exception)] for every raw exception that escaped."""
    raw, n = [], 0
    while True:
        fs = make_fs()
        count = [0]
        if mode == 'read':
            def read_fail(op, path, n=n):
                count[0] += 1
                return oserror(errno.EIO) if count[0] == n + 1 else None
            f = FaultFs(fs, read_fail=read_fail)
        else:
            def fail(op, path, i, n=n):
                count[0] += 1
                return oserror(errno.EIO) if i == n else None
            f = FaultFs(fs, fail=fail)
        try:
            r = open_store(MEM_BASE, account(), exchange=ex_factory(), now_ms=T, fs=f, cipher=CIPHER)
            _close(r)
        except Exception as ex:                                   # noqa: BLE001 - the point is: nothing escapes
            raw.append((n, f'{type(ex).__name__}: {ex}'))
        if count[0] <= n:                                         # the n-th call never happened: swept every call
            return raw, n
        n += 1


def _managed_fs():
    fs, _, _ = managed_with_lot()
    return fs


@pytest.mark.parametrize('mode', ['read', 'mut'])
@pytest.mark.parametrize('exchange', ['match', 'mismatch'])            # MANAGE, and HOLD (commits a HOLD generation)
def test_no_raw_exception_leaves_open_store_on_an_existing_store(mode, exchange):
    _, pos, orders = owned()
    ex = (lambda: FakeExchange(pos, orders)) if exchange == 'match' else (lambda: FakeExchange([], []))
    raw, n = _sweep(_managed_fs, ex, mode)
    assert n > (5 if mode == 'read' else 1) and raw == []          # a MANAGE boot makes few writes


@pytest.mark.parametrize('mode', ['read', 'mut'])
@pytest.mark.parametrize('flat', [True, False])
def test_no_raw_exception_leaves_open_store_at_init(mode, flat):
    _, pos, orders = owned()
    raw, n = _sweep(mem, (lambda: FakeExchange()) if flat else (lambda: FakeExchange(pos, orders)), mode)
    assert n > (5 if mode == 'read' else 1) and raw == []          # a MANAGE boot makes few writes


# ---------------------------------------------------------------------------------------------------- N4
@pytest.mark.parametrize('stray', ['notes.txt', 'g1.snap.bak', 'Thumbs.db', 'dir'])
def test_a_stray_file_in_snap_is_reported_and_ignored_never_damage_never_deleted(stray):
    fs, ex, _ = managed_with_lot()
    p = os.path.join(P.snap, stray)
    if stray == 'dir':
        fs.mkdir(p)
    else:
        _put(fs, p, b'not a snapshot')
    r = go(fs, ex)
    assert r.mode is Mode.MANAGE, r.items
    assert any('stray' in f and stray in f for f in r.findings), r.findings
    assert fs.kind(p) == ('dir' if stray == 'dir' else 'file')
    _close(r)
    r2 = go(fs, ex)                                              # and every later boot too (nothing was moved)
    assert r2.mode is Mode.MANAGE and fs.kind(p) != 'missing'
    _close(r2)


# ---------------------------------------------------------------------------------------------------- N7
BAD_ENTRIES = {
    'garbage': b'\x00\xffnot json',
    'not_canonical': b'{ "account_id": "' + ACCT.encode() + b'", "binding_digest": "' + DIGEST.encode() + b'" }',
    'wrong_shape': canonical_json({'owner': ACCT}),
    'other_digest_inside': canonical_json({'account_id': ACCT, 'binding_digest': OTHER_DIGEST}),
    'other_account': canonical_json({'account_id': OTHER_ACCT, 'binding_digest': DIGEST}),
}


@pytest.mark.parametrize('entry', sorted(BAD_ENTRIES))
def test_a_bad_by_binding_entry_blocks_a_flat_init(entry):
    fs = mem()
    _put(fs, os.path.join(P.by_binding, DIGEST), BAD_ENTRIES[entry])
    before = data_files(fs)
    r = go(fs, FakeExchange())
    assert r.mode is Mode.HOLD and any(i.cause == 'identity' for i in r.items), (r.mode, r.items)
    assert data_files(fs) == before                               # INIT wrote nothing
    _close(r)


@pytest.mark.parametrize('entry', sorted(BAD_ENTRIES))
def test_a_bad_by_binding_entry_blocks_manage_at_boot(entry):
    fs, ex, _ = managed_with_lot()
    p = os.path.join(P.by_binding, DIGEST)
    fs.files.pop(os.path.normpath(p))
    _put(fs, p, BAD_ENTRIES[entry])
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and any(i.cause == 'identity' for i in r.items), (r.mode, r.items)
    _close(r)


def test_an_unreadable_by_binding_entry_is_hold_with_no_write():
    fs, ex, _ = managed_with_lot()
    p = os.path.normcase(os.path.normpath(os.path.join(P.by_binding, DIGEST)))
    f = FaultFs(fs, read_fail=lambda op, q: oserror(errno.EIO)
                if os.path.normcase(os.path.normpath(q)) == p and op == 'read_bytes' else None)
    before = fs.snapshot()
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=CIPHER)
    assert r.mode is Mode.HOLD and r.items
    after = fs.snapshot()
    assert {k: v for k, v in after.items() if 'incidents' not in k and not k.endswith('.lock')} == \
        {k: v for k, v in before.items() if 'incidents' not in k and not k.endswith('.lock')}
    _close(r)


def test_bind_never_overwrites_an_entry_of_another_account():
    fs = mem()
    r = go(fs, FakeExchange())
    st = r.store
    p = os.path.join(P.by_binding, DIGEST)
    fs.files.pop(os.path.normpath(p))
    _put(fs, p, BAD_ENTRIES['other_account'])
    assert st.bind(T) is False
    assert fs.read_bytes(p) == BAD_ENTRIES['other_account']
    _close(r)


def test_an_owner_promotion_onto_a_key_bound_to_another_account_is_refused_before_any_commit():
    fs, ex, pf = managed_with_lot()
    _put(fs, os.path.join(P.by_binding, OTHER_DIGEST),
         canonical_json({'account_id': OTHER_ACCT, 'binding_digest': OTHER_DIGEST}))
    other = account(OTHER_DIGEST)
    ex2 = FakeExchange(ex.positions, ex.orders, digest=OTHER_DIGEST)
    r = go(fs, ex2, other)
    assert r.mode is Mode.HOLD
    gen = r.store.current.generation
    res = r.store.promote(ex2, T, account=other, owner_decision_id=DEC)
    assert res.mode is Mode.HOLD and any(i.cause == 'identity' for i in res.items), res.items
    assert r.store.current.generation == gen                      # nothing committed
    _close(r)


# ---------------------------------------------------------------------------------------------------- N6
def test_real_fs_envelope_with_the_portable_cipher_runs_everywhere(tmp_path):
    root = str(tmp_path / ACCT)
    os.mkdir(root)
    fs = RealFs()
    secret = b'portable evidence bytes ' * 4
    ref, created = write_envelope(fs, root, ACCT, secret, source='damaged_member', rel_path='snap/g1.snap',
                                  cipher=CIPHER)
    assert created
    with open(os.path.join(root, *ref.name.split('/')), 'rb') as fh:
        raw = fh.read()
    assert secret not in raw
    meta, plain = read_envelope(raw, CIPHER, ACCT)
    assert plain == secret and meta['cipher'] == CIPHER.name
    acl = fs.read_acl(os.path.join(root, 'evidence'))
    if sys.platform == 'win32':
        assert acl.startswith('D:P')
    else:
        assert acl == '0o700'


def test_the_nf_fixture_passes_are_not_skipped_off_windows():
    import test_nc02b_nf_fixtures as nf
    marks = getattr(nf, 'pytestmark', [])
    marks = marks if isinstance(marks, list) else [marks]
    assert not any(m.name == 'skipif' for m in marks), 'the NF fixture passes must run on every OS (portable cipher)'


OWN = canonical_json({'account_id': ACCT, 'binding_digest': DIGEST})


@pytest.mark.parametrize('torn', ['empty', 'prefix', 'prefix_nul_filled'])
def test_a_torn_own_entry_is_an_interrupted_bind_completed_in_place(torn):
    """A crash inside this account's own bind() leaves a prefix of its canonical entry (NUL-filled under the zero model):
    not an identity problem - boot proceeds and the next bind() completes the entry (it never touches anything else)."""
    fs, ex, _ = managed_with_lot()
    p = os.path.join(P.by_binding, DIGEST)
    fs.files.pop(os.path.normpath(p))
    raw = {'empty': b'', 'prefix': OWN[:17], 'prefix_nul_filled': OWN[:17] + bytes(len(OWN) - 17)}[torn]
    _put(fs, p, raw)
    r = go(fs, ex)
    assert r.mode is Mode.MANAGE, r.items
    assert r.store.bind(T) is True and fs.read_bytes(p) == OWN
    _close(r)


def test_a_prefix_that_diverges_from_the_own_entry_is_not_torn():
    fs, ex, _ = managed_with_lot()
    p = os.path.join(P.by_binding, DIGEST)
    fs.files.pop(os.path.normpath(p))
    other = canonical_json({'account_id': OTHER_ACCT, 'binding_digest': DIGEST})
    _put(fs, p, other[:30])
    assert other[:30] != OWN[:30]
    r = go(fs, ex)
    assert r.mode is Mode.HOLD and any(i.cause == 'identity' for i in r.items)
    assert r.store.bind(T) is False and fs.read_bytes(p) == other[:30]
    _close(r)


@pytest.mark.parametrize('where,item', [('anchors', 'anchors directory'), ('snap', 'snap directory'),
                                        ('account', 'account path unreadable')])
def test_a_failing_kind_names_the_member_it_could_not_read(where, item):
    """N3, precise: the failing path is named in the rule-3 item (not only caught by the read-phase net)."""
    fs, ex, _ = managed_with_lot()
    target = os.path.normcase(os.path.normpath({'anchors': P.anchors, 'snap': P.snap, 'account': P.account}[where]))
    f = FaultFs(fs, read_fail=lambda op, q: oserror(errno.EIO)
                if op == 'kind' and os.path.normcase(os.path.normpath(q)) == target else None)
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=CIPHER)
    assert r.mode is Mode.HOLD and r.writes == frozenset()                 # rule 3: no write at all
    assert any(i.cause == 'unreadable' and item in i.ref for i in r.items), r.items
    _close(r)
