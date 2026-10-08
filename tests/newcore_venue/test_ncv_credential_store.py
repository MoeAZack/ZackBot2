"""NEWCORE credential store (DPAPI, CurrentUser), the console entry tool and the secret scrubber.
DUMMY values only: no real key is ever read, printed or used here, and the legacy config.env is never touched."""
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
from decimal import Decimal as D

import pytest

from newcore.venue.credentials import (STORE_MAGIC, CredentialsUnavailable, CredentialStore, CredentialStoreError,
                                       DpapiProtector, MainnetCredentialRefused, SecretScrubber, StoredCredentials,
                                       default_root, key_digest, mask_key)
from newcore.venue.transport import BinanceTestnetTransport, PositionMode

from ncv_support import FakeHttp

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WINDOWS = os.name == 'nt'
needs_dpapi = pytest.mark.skipif(not WINDOWS, reason='DPAPI is Windows-only')

# Dummy values only (Binance's public docs example pair, plus two obviously fake ones).
KEY = 'dbefbc809e3e83c283a984c3a1459732ea7db1360ca80c5c2c8867408d28cc83'
SECRET = '2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9'
KEY2 = 'DUMMYROTATEDKEY0000000000000000000000000000000000000000000000001'
SECRET2 = 'DUMMYROTATEDSECRET000000000000000000000000000000000000000000002'
ACCOUNT = 'nc-acct-0001'
NOW = 1759917600000


def load_tool():
    spec = importlib.util.spec_from_file_location('newcore_keys_tool', os.path.join(REPO, 'tools', 'newcore_keys.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class XorProtector:
    """NOT a protector: a reversible stand-in that ignores entropy, so record-level checks (shape, account binding,
    environment) can be reached on any platform. Never used for the plaintext/grep checks."""

    def protect(self, data, entropy):
        return b'X' + bytes(b ^ 0x5A for b in data)

    def unprotect(self, data, entropy):
        if not data.startswith(b'X'):
            raise CredentialsUnavailable('bad', 'undecryptable')
        return bytes(b ^ 0x5A for b in data[1:])


def fake_store(tmp_path, account=ACCOUNT):
    return CredentialStore(account, root=str(tmp_path / 'secrets'), protector=XorProtector(), harden_acl=False)


def write_fake_record(store, **override):
    rec = {'v': 1, 'environment': 'testnet', 'account_id': store.account_id, 'api_key': KEY, 'api_secret': SECRET,
           'key_digest': key_digest(KEY), 'created_ms': NOW, 'previous_key_digest': None}
    rec.update(override)
    os.makedirs(store.root, exist_ok=True)
    with open(store.path, 'wb') as fh:
        fh.write(STORE_MAGIC + XorProtector().protect(json.dumps(rec).encode(), b''))


def file_has_plaintext(path, *values):
    data = open(path, 'rb').read()
    return any(v.encode('ascii') in data or v.encode('utf-16-le') in data for v in values)


# ---------- DPAPI round trip ----------

@needs_dpapi
def test_dpapi_round_trip_and_no_plaintext_on_disk(tmp_path):
    store = CredentialStore(ACCOUNT, root=str(tmp_path / 'secrets'))
    info = store.save('testnet', KEY, SECRET, now_ms=NOW)
    assert info.masked_key == 'dbef…cc83' and info.key_digest == key_digest(KEY) and not info.rotated
    assert os.path.basename(store.path) == ACCOUNT + '.bin'
    assert open(store.path, 'rb').read().startswith(STORE_MAGIC)
    assert not file_has_plaintext(store.path, KEY, SECRET)
    creds = store.load()
    assert isinstance(creds, StoredCredentials) and creds.account_id == ACCOUNT and creds.environment == 'testnet'
    # Binance docs signing vector through the loaded source (proves the secret round-tripped exactly).
    q = (b'symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000&timeInForce=GTC'
         b'&recvWindow=5000&timestamp=1591702613943')
    assert creds.sign(q) == '3c661234138461fcc7a7d8746c6558c9842d4e10870d2ecbedf7777cad694af9'
    assert KEY not in repr(creds) and SECRET not in repr(creds)
    assert os.listdir(store.root) == [ACCOUNT + '.bin']             # atomic write left no temp file


@needs_dpapi
def test_dpapi_blob_is_bound_to_its_account(tmp_path):
    root = str(tmp_path / 'secrets')
    a = CredentialStore(ACCOUNT, root=root, harden_acl=False)
    a.save('testnet', KEY, SECRET, now_ms=NOW)
    b = CredentialStore('nc-acct-0002', root=root, harden_acl=False)
    os.replace(a.path, b.path)                                      # someone renames / copies the file
    with pytest.raises(CredentialsUnavailable) as ei:
        b.load()
    # First line: the DPAPI entropy is bound to the AccountId, so the blob does not even decrypt. (The record's own
    # account_id check is the second line; it is covered with the XorProtector in test_bad_record_is_typed.)
    assert ei.value.reason == 'undecryptable'


@needs_dpapi
def test_dpapi_entropy_must_match():
    p = DpapiProtector()
    blob = p.protect(b'dummy-plaintext', b'entropy-A')
    assert p.unprotect(blob, b'entropy-A') == b'dummy-plaintext'
    with pytest.raises(CredentialsUnavailable) as ei:
        p.unprotect(blob, b'entropy-B')
    assert ei.value.reason == 'undecryptable'


@needs_dpapi
@pytest.mark.parametrize('mangle', [lambda b: b[:len(STORE_MAGIC) + 10], lambda b: b[:-1] + bytes([b[-1] ^ 1]),
                                    lambda b: STORE_MAGIC + b'garbage'])
def test_dpapi_tampered_blob_is_undecryptable(tmp_path, mangle):
    store = CredentialStore(ACCOUNT, root=str(tmp_path / 'secrets'), harden_acl=False)
    store.save('testnet', KEY, SECRET, now_ms=NOW)
    blob = open(store.path, 'rb').read()
    open(store.path, 'wb').write(mangle(blob))
    with pytest.raises(CredentialsUnavailable) as ei:
        store.load()
    assert ei.value.reason == 'undecryptable'


@needs_dpapi
def test_acl_restricted_to_current_user(tmp_path):
    store = CredentialStore(ACCOUNT, root=str(tmp_path / 'secrets'))
    store.save('testnet', KEY, SECRET, now_ms=NOW)
    assert store.acl_restricted is True
    listing = subprocess.run(['icacls', store.root], capture_output=True, text=True).stdout
    aces = [ln for ln in listing.splitlines()[:-2] if ':' in ln]
    assert len(aces) == 1 and '(I)' not in listing                 # one ACE (this user), nothing inherited


# ---------- typed failures (any platform) ----------

def test_missing_store_is_typed(tmp_path):
    with pytest.raises(CredentialsUnavailable) as ei:
        fake_store(tmp_path).load()
    assert ei.value.reason == 'missing'


@pytest.mark.parametrize('content,reason', [(b'', 'corrupt'), (b'NOTMAGIC' + b'x' * 20, 'corrupt'),
                                            (STORE_MAGIC, 'corrupt'), (STORE_MAGIC + b'Y123', 'undecryptable')])
def test_corrupt_file_is_typed(tmp_path, content, reason):
    store = fake_store(tmp_path)
    os.makedirs(store.root)
    open(store.path, 'wb').write(content)
    with pytest.raises(CredentialsUnavailable) as ei:
        store.load()
    assert ei.value.reason == reason


@pytest.mark.parametrize('override,reason', [
    (dict(v=2), 'corrupt'), (dict(extra=1), 'corrupt'), (dict(key_digest='zbk1:00'), 'corrupt'),
    (dict(api_secret=''), 'corrupt'), (dict(created_ms='now'), 'corrupt'),
    (dict(account_id='nc-acct-9999'), 'account_mismatch'), (dict(environment='mainnet'), 'environment_refused'),
])
def test_bad_record_is_typed(tmp_path, override, reason):
    store = fake_store(tmp_path)
    write_fake_record(store, **override)
    with pytest.raises(CredentialsUnavailable) as ei:
        store.load()
    assert ei.value.reason == reason
    assert SECRET not in str(ei.value) and KEY not in str(ei.value)


def test_record_without_a_field_is_corrupt(tmp_path):
    store = fake_store(tmp_path)
    os.makedirs(store.root)
    rec = {'v': 1, 'environment': 'testnet', 'account_id': ACCOUNT, 'api_key': KEY, 'api_secret': SECRET}
    open(store.path, 'wb').write(STORE_MAGIC + XorProtector().protect(json.dumps(rec).encode(), b''))
    with pytest.raises(CredentialsUnavailable) as ei:
        store.info()
    assert ei.value.reason == 'corrupt'


def test_unsupported_platform_is_typed(tmp_path, monkeypatch):
    store = CredentialStore(ACCOUNT, root=str(tmp_path / 'secrets'), harden_acl=False)
    os.makedirs(store.root)
    open(store.path, 'wb').write(STORE_MAGIC + b'blob')
    monkeypatch.setattr(DpapiProtector, 'available', staticmethod(lambda: False))
    with pytest.raises(CredentialsUnavailable) as ei:
        store.load()
    assert ei.value.reason == 'unsupported_platform'


def test_transport_with_missing_store_fails_typed_and_sends_nothing(tmp_path):
    http = FakeHttp()
    with pytest.raises(CredentialsUnavailable):
        BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW, position_mode=PositionMode.HEDGE,
                                credentials=fake_store(tmp_path).load())
    assert http.requests == []


# ---------- mainnet / environment / roots ----------

def test_mainnet_refused_and_nothing_written(tmp_path):
    store = fake_store(tmp_path)
    with pytest.raises(MainnetCredentialRefused):
        store.save('mainnet', KEY, SECRET, now_ms=NOW)
    assert not os.path.exists(store.root)


@pytest.mark.parametrize('env', ['live', 'TESTNET', None, ''])
def test_other_environments_refused(tmp_path, env):
    with pytest.raises(CredentialStoreError):
        fake_store(tmp_path).save(env, KEY, SECRET, now_ms=NOW)


@pytest.mark.parametrize('acct', ['', '../x', 'a b', 'a/b', '-x', 'x' * 65, None, 'nc-acct-0001\n'])
def test_account_id_grammar(tmp_path, acct):
    with pytest.raises(CredentialStoreError):
        CredentialStore(acct, root=str(tmp_path))


def test_root_inside_legacy_data_folder_refused(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    with pytest.raises(CredentialStoreError):
        CredentialStore(ACCOUNT, root=str(tmp_path / 'ZackBot' / 'secrets'))
    with pytest.raises(CredentialStoreError):
        CredentialStore(ACCOUNT, root=str(tmp_path / 'ZackBot'))
    CredentialStore(ACCOUNT, root=str(tmp_path / 'ZackBotNC' / 'secrets'))       # the separate root is fine


def test_root_inside_repo_refused():
    with pytest.raises(CredentialStoreError):
        CredentialStore(ACCOUNT, root=os.path.join(REPO, 'secrets'))


def test_default_root_is_zackbotnc(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert default_root() == os.path.join(str(tmp_path), 'ZackBotNC', 'secrets')
    assert CredentialStore(ACCOUNT).path == os.path.join(str(tmp_path), 'ZackBotNC', 'secrets', ACCOUNT + '.bin')
    monkeypatch.delenv('LOCALAPPDATA')
    with pytest.raises(CredentialsUnavailable) as ei:
        default_root()
    assert ei.value.reason == 'not_configured'


def test_rotation_keeps_account_and_reports_previous_digest(tmp_path):
    store = fake_store(tmp_path)
    assert not store.save('testnet', KEY, SECRET, now_ms=NOW).rotated
    info = store.save('testnet', KEY2, SECRET2, now_ms=NOW + 1)
    assert info.rotated and info.previous_key_digest == key_digest(KEY) and info.account_id == ACCOUNT
    assert store.info().previous_key_digest == key_digest(KEY)
    assert store.save('testnet', KEY2, SECRET2, now_ms=NOW + 2).previous_key_digest == key_digest(KEY)


def test_write_failure_leaves_nothing(tmp_path):
    class Failing(XorProtector):
        def protect(self, data, entropy):
            raise CredentialStoreError('DPAPI could not protect the record')
    store = CredentialStore(ACCOUNT, root=str(tmp_path / 's'), protector=Failing(), harden_acl=False)
    with pytest.raises(CredentialStoreError):
        store.save('testnet', KEY, SECRET, now_ms=NOW)
    assert not os.path.exists(store.path)


def test_clear(tmp_path):
    store = fake_store(tmp_path)
    store.save('testnet', KEY, SECRET, now_ms=NOW)
    assert store.clear() is True and store.clear() is False
    with pytest.raises(CredentialsUnavailable):
        store.load()


def test_mask_key():
    assert mask_key('abcdefghijklmnopwxyz') == 'abcd…wxyz' and mask_key('short') == '…' and mask_key(None) == '…'


# ---------- the entry tool ----------

def run_tool(argv, answers=(), **kw):
    tool = load_tool()
    asked = []

    def prompt(text):
        asked.append(text)
        return answers[len(asked) - 1]
    out = io.StringIO()
    rc = tool.main(argv, prompt=prompt, out=out, **kw)
    return rc, out.getvalue(), asked


@pytest.mark.parametrize('argv', [
    ['set', '--env', 'testnet', '--account-id', ACCOUNT, '--api-key', 'x'],
    ['set', '--env', 'testnet', '--account-id', ACCOUNT, '--secret=abc'],
    ['set', '--env', 'testnet', '--account-id', ACCOUNT, '--key', 'abc'],
    ['set', '--env', 'testnet', '--account-id', ACCOUNT, KEY],
    ['set', '--env', 'testnet', '--account-id', ACCOUNT, '--password', 'p'],
    ['status', '--account-id', ACCOUNT, '-token=t'],
])
def test_tool_refuses_keys_on_command_line(tmp_path, argv):
    rc, out, asked = run_tool(argv + ['--root', str(tmp_path / 's')], protector=XorProtector(), harden_acl=False)
    assert rc == 2 and asked == [] and 'REFUSED' in out
    assert KEY not in out and not os.path.exists(tmp_path / 's')


def test_tool_refuses_mainnet_without_prompting(tmp_path):
    rc, out, asked = run_tool(['set', '--env', 'mainnet', '--account-id', ACCOUNT, '--root', str(tmp_path / 's')],
                              protector=XorProtector(), harden_acl=False)
    assert rc == 3 and asked == [] and not os.path.exists(tmp_path / 's')


def test_tool_set_status_clear_masked(tmp_path):
    root = str(tmp_path / 's')
    scrub = SecretScrubber()
    try:
        rc, out, asked = run_tool(['set', '--env', 'testnet', '--account-id', ACCOUNT, '--root', root],
                                  answers=(KEY, SECRET), now_ms=NOW, protector=XorProtector(), harden_acl=False,
                                  scrubber=scrub)
    finally:
        scrub.uninstall()
    assert rc == 0 and len(asked) == 2 and 'stored' in out and 'does NOT enable trading' in out
    rc, out, _ = run_tool(['status', '--account-id', ACCOUNT, '--root', root], protector=XorProtector())
    assert rc == 0
    assert 'environment : testnet' in out and 'dbef…cc83' in out and key_digest(KEY) in out
    # 1759917600000 ms = 2025-10-08 10:00 UTC = 13:00 Cairo (EEST, UTC+3). UTC-only fallback when tz data is absent.
    assert '2025-10-08 13:00 Cairo (10:00 UTC)' in out or '2025-10-08 10:00 UTC' in out
    assert KEY not in out and SECRET not in out
    rc, out, _ = run_tool(['clear', '--account-id', ACCOUNT, '--root', root], protector=XorProtector())
    assert rc == 2 and os.path.exists(os.path.join(root, ACCOUNT + '.bin'))      # --yes required
    rc, out, _ = run_tool(['clear', '--account-id', ACCOUNT, '--root', root, '--yes'], protector=XorProtector())
    assert rc == 0 and 'removed' in out
    rc, out, _ = run_tool(['status', '--account-id', ACCOUNT, '--root', root], protector=XorProtector())
    assert rc == 4 and 'missing' in out


def test_tool_status_shows_rotation(tmp_path):
    store = fake_store(tmp_path)
    store.save('testnet', KEY, SECRET, now_ms=NOW)
    store.save('testnet', KEY2, SECRET2, now_ms=NOW)
    rc, out, _ = run_tool(['status', '--account-id', ACCOUNT, '--root', store.root], protector=XorProtector())
    assert rc == 0 and 'ROTATED' in out and 'UNCONFIRMED' in out and KEY2 not in out


def test_tool_refuses_legacy_root(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    rc, out, asked = run_tool(['status', '--account-id', ACCOUNT, '--root', str(tmp_path / 'ZackBot' / 'x')])
    assert rc == 2 and 'REFUSED' in out


# ---------- the scrubber ----------

def test_scrubber_redacts_record_traceback_and_repr(capsys):
    scrub = SecretScrubber()
    scrub.register(SECRET, KEY)
    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    handler.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    lg = logging.getLogger('ncv.scrub.test')
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    lg.propagate = False

    class Holder:
        def __repr__(self):
            return f'Holder(secret={SECRET})'
    try:
        scrub.install()
        lg.info('plain %s and %r', SECRET, Holder())
        lg.warning(f'f-string {KEY}')
        try:
            raise RuntimeError(f'boom {SECRET}')
        except RuntimeError:
            lg.exception('failed with %s', 'context')
        lg.info('stack', stack_info=True)
        try:
            raise ValueError(f'uncaught {SECRET}')
        except ValueError:
            sys.excepthook(*sys.exc_info())
    finally:
        scrub.uninstall()
        lg.removeHandler(handler)
    text = sink.getvalue() + capsys.readouterr().err
    assert 'boom <redacted>' in text and 'Holder(secret=<redacted>)' in text and 'uncaught <redacted>' in text
    assert 'Traceback' in text
    assert SECRET not in text and KEY not in text
    assert scrub.scrub(f'x{SECRET}y') == 'x<redacted>y' and SECRET not in repr(scrub)


def test_scrubber_covers_handlers_added_after_install():
    scrub = SecretScrubber()
    scrub.register(SECRET)
    sink = io.StringIO()
    lg = logging.getLogger('ncv.scrub.late')
    lg.propagate = False
    try:
        scrub.install()
        h = logging.StreamHandler(sink)
        lg.addHandler(h)
        lg.error('late %s', SECRET)
    finally:
        scrub.uninstall()
        lg.removeHandler(h)
    assert SECRET not in sink.getvalue() and '<redacted>' in sink.getvalue()


def test_scrubber_uninstall_restores_hooks():
    before_factory, before_hook = logging.getLogRecordFactory(), sys.excepthook
    s = SecretScrubber()
    s.register(SECRET)
    s.install()
    s.uninstall()
    assert logging.getLogRecordFactory() is before_factory and sys.excepthook is before_hook


# ---------- grep: a full set / status / use cycle leaves the secret in no file ----------

@needs_dpapi
def test_grep_full_cycle_secret_only_in_encrypted_blob(tmp_path, capsys):
    root = str(tmp_path / 'secrets')
    log_path = tmp_path / 'run.log'
    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    rootlog = logging.getLogger()
    old_level = rootlog.level
    rootlog.addHandler(fh)
    rootlog.setLevel(logging.DEBUG)
    scrub = SecretScrubber()
    outputs = []
    try:
        rc, out, _ = run_tool(['set', '--env', 'testnet', '--account-id', ACCOUNT, '--root', root],
                              answers=(KEY, SECRET), now_ms=NOW, scrubber=scrub)
        assert rc == 0
        outputs.append(out)
        rc, out, _ = run_tool(['status', '--account-id', ACCOUNT, '--root', root])
        assert rc == 0
        outputs.append(out)
        creds = CredentialStore(ACCOUNT, root=root).load()           # "use": sign real requests through the store
        http = FakeHttp('account_v2', 'order_market_filled', RuntimeError(f'leak attempt {SECRET} {KEY}'))
        t = BinanceTestnetTransport(environment='testnet', http=http, clock=lambda: NOW,
                                    position_mode=PositionMode.HEDGE, credentials=creds)
        log = logging.getLogger('ncv.grep')
        log.debug('account %r', t.account())
        log.debug('order %r', t.place_market('SOLUSDT', 'BUY', 'LONG', D('10'), 'zb-ABCDEFGHIJKLMNOPQRSTUVWX',
                                             reduce_only=False))
        log.debug('creds %r transport %r request %r', creds, t, http.requests[0])
        log.debug('unknown %r', t.account())
        try:
            raise RuntimeError(f'simulated crash holding {SECRET}')
        except RuntimeError:
            log.exception('crash')
        assert http.requests[0].header('X-MBX-APIKEY') == KEY            # the wire really carried the key
    finally:
        scrub.uninstall()
        rootlog.removeHandler(fh)
        fh.close()
        rootlog.setLevel(old_level)
    captured = capsys.readouterr()
    for text in outputs + [captured.out, captured.err]:
        assert SECRET not in text and KEY not in text
    files = [os.path.join(d, f) for d, _, fs in os.walk(tmp_path) for f in fs]
    blob = os.path.join(root, ACCOUNT + '.bin')
    assert blob in files and str(log_path) in files
    log_text = open(log_path, encoding='utf-8').read()
    assert 'crash' in log_text and '<redacted>' in log_text                 # the log was written and scrubbed
    for path in files:
        assert not file_has_plaintext(path, SECRET), path                # not even inside the encrypted blob
        assert not file_has_plaintext(path, KEY), path
    assert os.path.getsize(log_path) > 0
