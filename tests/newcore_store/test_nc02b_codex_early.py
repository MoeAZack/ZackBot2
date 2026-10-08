"""Repros for Codex's early review of nc-02b e4c38f3 (#13), written before the fixes.

P1 TOCTOU: the by-binding entry was read twice per boot (once to classify the blocking problem, once more only to
   detect a torn entry); a change between the reads made the second classification win silently. Now ONE read per
   classification: the blocking problem and the torn finding come from the same immutable result.
P2 failure_reason (and the other store sinks) never echo an exception's message into durable findings / HOLD detail:
   the classification is by type and the __cause__ chain only, the output is a fixed public reason plus safe fields
   (exception type, errno, winerror). No string matching on messages.
"""
import errno
import os

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from nc02a_memfs import FaultFs
from nc02a_util import TEST_CIPHER
from nc02b_helpers import ACCT, DIGEST, MEM_BASE, T, account
from newcore.store import Verdict, create_journal, recover_journal
from newcore.store import cipher as cipher_mod
from newcore.store.cipher import CipherUnavailable
from newcore.store.errors import DurabilityUnavailable, failure_reason
from newcore.store.header import canonical_json
from newcore.store.store import Mode, Paths, boot
from test_nc02b_store import managed_with_lot

P = Paths(MEM_BASE, ACCT)
ENTRY = os.path.normcase(os.path.normpath(os.path.join(P.by_binding, DIGEST)))
OTHER = canonical_json({'account_id': 'acct_' + 'c3' * 16, 'binding_digest': DIGEST})
SECRET = 'sk_live_TOKEN42-hunter2'


class _ChangesAfterFirstRead(FaultFs):
    """The by-binding entry changes right after its first read (a concurrent writer / a hostile FS)."""

    def __init__(self, inner, after):
        super().__init__(inner)
        self.reads, self.after = 0, after

    def read_bytes(self, p):
        data = self.inner.read_bytes(p)
        if os.path.normcase(os.path.normpath(p)) == ENTRY:
            self.reads += 1
            if self.reads == 1:
                self.inner.files[os.path.normpath(p)].current = self.after
        return data


# ---------------------------------------------------------------------------------------------------- P1
def test_the_by_binding_entry_is_read_once_per_boot_and_one_read_decides_everything():
    fs, ex, _ = managed_with_lot()
    p = os.path.normpath(os.path.join(P.by_binding, DIGEST))
    fs.files[p].current = b''                                     # torn own entry at the first read ...
    f = _ChangesAfterFirstRead(fs, OTHER)                         # ... another account's entry right after it
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=TEST_CIPHER)
    assert f.reads == 1, f'the entry was read {f.reads} times'
    assert any('by-binding entry torn' in x for x in r.findings), r.findings          # derived from that one read
    if r.store is not None and r.store.journal is not None:
        r.store.journal.close()


def test_a_blocking_first_read_is_never_overridden_by_a_second():
    fs, ex, _ = managed_with_lot()
    p = os.path.normpath(os.path.join(P.by_binding, DIGEST))
    fs.files[p].current = OTHER                                   # another account at the first read
    f = _ChangesAfterFirstRead(fs, b'')
    r = boot(MEM_BASE, account(), exchange=ex, now_ms=T, fs=f, cipher=TEST_CIPHER)
    assert f.reads == 1 and r.mode is Mode.HOLD and any(i.cause == 'identity' for i in r.items)
    if r.store is not None and r.store.journal is not None:
        r.store.journal.close()


# ---------------------------------------------------------------------------------------------------- P2
@pytest.mark.parametrize('ex,kind', [
    (OSError(errno.EIO, SECRET, os.path.join('C:\\', 'Users', 'bob', SECRET + '.txt')), 'unwritable'),
    (DurabilityUnavailable('write', os.path.join('C:\\', 'Users', 'bob', 'x.seg'), OSError(errno.ENOSPC, SECRET)),
     'unwritable'),
    (CipherUnavailable(78, SECRET), 'cipher_unavailable'),
    (DurabilityUnavailable('evidence', 'x.ev', CipherUnavailable(78, SECRET)), 'cipher_unavailable'),
    (ValueError('CipherUnavailable ' + SECRET), 'unwritable'),          # no string matching on messages
    (RuntimeError(SECRET), 'unwritable'),
])
def test_failure_reason_is_typed_and_never_echoes_a_message(ex, kind):
    k, text = failure_reason(ex)
    assert k == kind
    assert SECRET not in text and 'bob' not in text and 'Users' not in text, text


def test_a_wrapped_cause_chain_is_classified_by_type():
    try:
        try:
            raise CipherUnavailable(78, SECRET)
        except CipherUnavailable as inner:
            raise OSError(errno.EIO, 'wrapper ' + SECRET) from inner
    except OSError as outer:
        k, text = failure_reason(outer)
    assert k == 'cipher_unavailable' and SECRET not in text


def test_a_cipherless_repair_keeps_the_cipher_message_out_of_findings_and_the_view(tmp_path, monkeypatch):
    def unavailable(self):
        raise CipherUnavailable(78, SECRET)
    monkeypatch.setattr(cipher_mod.DpapiCipher, '__init__', unavailable)
    os.mkdir(tmp_path / 'accounts')
    acct = str(tmp_path / 'accounts' / ACCOUNT_ID)
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID, cipher=TEST_CIPHER)
    for e in SCENARIO[:4]:
        j.append(e)
    j.close()
    with open(os.path.join(acct, 'journal', 'seg-000001.seg'), 'ab') as fh:
        fh.write(b'\x5a\xb2\x0a\x00\xff\x00')
    r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)
    assert r.verdict is Verdict.DURABILITY_UNAVAILABLE
    assert any(f.kind == 'cipher_unavailable' for f in r.findings)
    blob = repr(r.findings) + r.view._why
    assert SECRET not in blob, blob
