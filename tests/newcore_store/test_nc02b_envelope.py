"""NC-02b evidence envelope (A04, A15, A16; D4, D5): DPAPI CurrentUser, protected DACL user + SYSTEM, metadata only in
clear, verify-before-count, no partial left behind."""
import json
import os
import sys

import pytest

from nc02a_util import dpapi_available

from nc02a_memfs import PRIVATE, FaultFs, MemFs, oserror
from nc02a_util import TEST_CIPHER
from newcore.store.cipher import CipherUnavailable, DpapiCipher, InsecureTestCipher
from newcore.store.envelope import (EnvelopeError, peek_version, read_envelope, write_envelope)
from newcore.store.frame import FILE_HEADER, KIND_EVIDENCE, RT_HEADER, frame, record_at
from newcore.store.header import VersionVerdict, canonical_json
from newcore.store.fs import RealFs

ACCT = 'acct_' + 'c3' * 16
OTHER = 'acct_' + 'd4' * 16
SECRET = b'api_secret=sk_live_THIS_MUST_NEVER_APPEAR_IN_CLEAR_' * 3
WIN = pytest.mark.skipif(not dpapi_available(), reason='DPAPI and the Windows DACL exist on Windows only '
                                                         '(or NC02B_NO_DPAPI=1 simulates their absence)')


def mem():
    root = os.path.join(os.path.abspath(os.sep), 'env-mem', ACCT)
    return MemFs(root), root


def test_the_insecure_cipher_refuses_production_construction():
    with pytest.raises(CipherUnavailable):
        InsecureTestCipher()


def test_envelope_round_trip_carries_only_metadata_in_clear():
    fs, root = mem()
    ref, created = write_envelope(fs, root, ACCT, SECRET, source='damaged_member', rel_path='snap/g1.snap',
                                  mtime_ms=1_791_400_000_000, incident_id='inc-1', reason='recovery.schema_invalid',
                                  cipher=TEST_CIPHER)
    assert created and ref.size == len(SECRET) and ref.rel_path == 'snap/g1.snap'
    raw = fs.read_bytes(os.path.join(root, *ref.name.split('/')))
    assert SECRET not in raw and b'sk_live' not in raw
    meta, plain = read_envelope(raw, TEST_CIPHER, ACCT)
    assert plain == SECRET and meta['sha256'] == ref.sha256 and meta['incident_id'] == 'inc-1'
    assert fs.read_acl(os.path.join(root, 'evidence')) == PRIVATE
    again, created2 = write_envelope(fs, root, ACCT, SECRET, source='damaged_member', rel_path='snap/g1.snap',
                                     mtime_ms=1_791_400_000_000, incident_id='inc-1', reason='recovery.schema_invalid',
                                     cipher=TEST_CIPHER)
    assert again == ref and not created2                                  # idempotent


@pytest.mark.parametrize('how', ['flip_cipher', 'flip_meta', 'truncate', 'other_account', 'trailing'])
def test_a_tampered_or_foreign_envelope_never_verifies(how):
    fs, root = mem()
    ref, _ = write_envelope(fs, root, ACCT, SECRET, source='torn_tail', rel_path='journal/seg-000001.seg',
                            cipher=TEST_CIPHER)
    raw = fs.read_bytes(os.path.join(root, *ref.name.split('/')))
    acct = ACCT
    if how == 'flip_cipher':
        raw = raw[:-60] + bytes([raw[-60] ^ 1]) + raw[-59:]
    elif how == 'flip_meta':
        raw = raw[:40] + bytes([raw[40] ^ 1]) + raw[41:]
    elif how == 'truncate':
        raw = raw[:-5]
    elif how == 'other_account':
        acct = OTHER
    else:
        raw = raw + b'\0'
    with pytest.raises(EnvelopeError):
        read_envelope(raw, TEST_CIPHER, acct)


@pytest.mark.parametrize('fail_op', ['write', 'fsync'])
def test_a_failed_copy_leaves_no_partial_envelope(fail_op):                       # A04 / D4
    fs, root = mem()
    f = FaultFs(fs, fail=lambda op, p, i: oserror(28) if op == fail_op and p.endswith('.zbe') else None)
    with pytest.raises(OSError):
        write_envelope(f, root, ACCT, SECRET, source='damaged_member', rel_path='snap/g1.snap', cipher=TEST_CIPHER)
    assert fs.listdir(os.path.join(root, 'evidence')) == []
    assert ('unlink_own_partial' in [op for _, op, _ in f.trace])


def test_a_future_envelope_header_is_rule_one():
    fs, root = mem()
    ref, _ = write_envelope(fs, root, ACCT, b'x', source='torn_tail', rel_path='journal/seg-000001.seg',
                            cipher=TEST_CIPHER)
    raw = fs.read_bytes(os.path.join(root, *ref.name.split('/')))
    assert peek_version(raw) is VersionVerdict.OK
    r = record_at(raw, FILE_HEADER.size)
    meta = json.loads(r.payload)
    meta['min_reader_version'] = 7
    fut = raw[:FILE_HEADER.size] + frame(RT_HEADER, canonical_json(meta)) + raw[r.end:]
    assert peek_version(fut) is VersionVerdict.FUTURE
    assert peek_version(raw[:5] + b'\x09' + raw[6:]) is VersionVerdict.UNKNOWN
    assert raw[4] == KIND_EVIDENCE


@WIN
def test_dpapi_round_trip_is_bound_to_the_account_entropy():
    c = DpapiCipher()
    sealed = c.seal(SECRET, ACCT)
    assert SECRET not in sealed and c.open(sealed, ACCT) == SECRET
    with pytest.raises(OSError):
        c.open(sealed, OTHER)


@WIN
def test_real_envelope_dir_has_a_protected_dacl_for_user_and_system_only(tmp_path):
    root = str(tmp_path / ACCT)
    os.mkdir(root)
    fs = RealFs()
    ref, _ = write_envelope(fs, root, ACCT, SECRET, source='damaged_member', rel_path='snap/g1.snap')
    sddl = fs.read_acl(os.path.join(root, 'evidence'))
    assert sddl.startswith('D:P')                                           # protected: no inheritance
    aces = sddl[3:].strip('()').split(')(')
    sids = {a.split(';')[-1] for a in aces}
    assert 'SY' in sids and len(sids) == 2 and 'BA' not in sids             # user + SYSTEM; Administrators excluded
    with open(os.path.join(root, *ref.name.split('/')), 'rb') as fh:
        raw = fh.read()
    assert SECRET not in raw
    meta, plain = read_envelope(raw, DpapiCipher(), ACCT)
    assert plain == SECRET and meta['cipher'] == 'dpapi-user-v1'
