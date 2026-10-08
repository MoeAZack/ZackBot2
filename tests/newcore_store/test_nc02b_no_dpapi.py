"""Cowork N6 (7af893a): without DPAPI (Linux, or NC02B_NO_DPAPI=1) a production call that needs the evidence cipher
fails closed with a TYPED, visible reason - never silent - and the whole store suite still passes there.
"""
import os
import subprocess
import sys

import pytest

from nc02a_events import ACCOUNT_ID, AGGREGATE_ID, SCENARIO
from newcore.store import Verdict, create_journal, recover_journal
from newcore.store import cipher as cipher_mod
from newcore.store.cipher import CipherUnavailable, default_cipher
from newcore.store.errors import failure_reason

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def no_dpapi(monkeypatch):
    def unavailable(self):
        raise CipherUnavailable(78, 'DPAPI exists on Windows only')
    monkeypatch.setattr(cipher_mod.DpapiCipher, '__init__', unavailable)


def test_the_default_cipher_is_typed_unavailable(no_dpapi):
    with pytest.raises(CipherUnavailable):
        default_cipher()
    assert failure_reason(CipherUnavailable(78, 'DPAPI exists on Windows only'))[0] == 'cipher_unavailable'


def test_a_torn_tail_repair_without_a_cipher_fails_closed_with_a_typed_reason(tmp_path, no_dpapi):
    os.mkdir(tmp_path / 'accounts')
    acct = str(tmp_path / 'accounts' / ACCOUNT_ID)
    from nc02a_util import TEST_CIPHER
    j = create_journal(acct, ACCOUNT_ID, AGGREGATE_ID, cipher=TEST_CIPHER)
    for e in SCENARIO[:4]:
        j.append(e)
    j.close()
    seg = os.path.join(acct, 'journal', 'seg-000001.seg')
    with open(seg, 'ab') as fh:
        fh.write(b'\x5a\xb2\x0a\x00\xff\x00')                    # a short record header: a torn tail
    with open(seg, 'rb') as fh:
        before = fh.read()
    r = recover_journal(acct, ACCOUNT_ID, AGGREGATE_ID)          # the production default: no cipher here
    assert r.verdict is Verdict.DURABILITY_UNAVAILABLE and r.journal is None
    kinds = {f.kind: f.detail for f in r.findings}
    assert 'cipher_unavailable' in kinds and 'DPAPI' in kinds['cipher_unavailable'], r.findings
    assert 'cipher' in r.view._why                                # the read-only view says why, too
    with open(seg, 'rb') as fh:
        assert fh.read() == before                                # nothing sealed, the tail untouched
    assert sorted(os.listdir(os.path.join(acct, 'journal'))) == ['.lock', 'seg-000001.seg']
    assert not os.path.exists(os.path.join(acct, 'evidence')) or os.listdir(os.path.join(acct, 'evidence')) == []


@pytest.mark.skipif(sys.platform != 'win32' or bool(os.environ.get('NC02B_NO_DPAPI')),
                    reason='off Windows (or inside the simulation) the normal run already IS the no-DPAPI run')
def test_the_whole_store_suite_passes_with_dpapi_unavailable():
    env = dict(os.environ, NC02B_NO_DPAPI='1')
    out = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q', '-o', 'addopts=',
                          'tests/newcore_store'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=1800)
    tail = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-500:]
    assert out.returncode == 0, tail + '\n' + '\n'.join(l for l in out.stdout.splitlines() if l.startswith('FAILED'))
