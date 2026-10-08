"""Credentials for the testnet transport: the source interface, an in-memory source, and the DPAPI-backed store.

Owner decision: NEWCORE uses a separate testnet account and key that the OWNER enters (tools/newcore_keys.py); Claude
never handles it. The transport only ever sees an object implementing CredentialSource:

    api_key() -> str              the X-MBX-APIKEY header value (sent on SIGNED requests only)
    sign(payload: bytes) -> str   lower-case hex HMAC-SHA256 of payload under the secret

key_digest() is a one-way, domain-separated fingerprint of the API key. It identifies a CREDENTIAL, not an exchange
account (Codex ruling 2026-10-08): the NEWCORE AccountId binding is provisioned before the first INIT, the store file
carries the AccountId it belongs to and refuses to attach to another one, and a rotated key keeps the AccountId but
leaves the binding unconfirmed (HOLD + reconcile + confirm, an engine decision; the store only reports the rotation).

CredentialStore (Windows DPAPI, CurrentUser scope, via ctypes/crypt32):
- default file %LOCALAPPDATA%\\ZackBotNC\\secrets\\<account_id>.bin; the root is injectable. A root inside the legacy
  %LOCALAPPDATA%\\ZackBot folder or inside the repository is refused.
- the record (JSON, then DPAPI-encrypted with account-bound entropy): v, environment, account_id, api_key, api_secret,
  key_digest, created_ms, previous_key_digest.
- writes are atomic (temp file + fsync + os.replace); the folder ACL is restricted to the current user with icacls
  (best effort: DPAPI is the protection; %LOCALAPPDATA% is already per-user. The result is reported, never fatal).
- missing / corrupt / undecryptable / wrong-account / wrong-environment -> CredentialsUnavailable(reason). No crash,
  no retry loop. The engine treats it as idle/HOLD.
- mainnet keys are refused (MainnetCredentialRefused). Storing a key never enables trading or live mode.
Nothing here needs credentials at import time; ctypes / subprocess are imported only when the store is used.
"""
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .redact import check_value, redact_values

KEY_DIGEST_PREFIX = 'zbk1:'
_DIGEST_DOMAIN = b'zackbot/newcore/venue/key-digest/v1\x00'
_REDACTED = '<redacted>'


class CredentialsUnavailable(Exception):
    """No usable credentials (none configured, store missing / corrupt / undecryptable / bound to another account,
    source failure). A clean typed failure: nothing was sent. `reason` is a stable token. The engine treats it as
    idle/HOLD; it is never retried in a loop here."""

    def __init__(self, message, reason='unavailable'):
        super().__init__(message)
        self.reason = reason


class MainnetCredentialRefused(Exception):
    """A mainnet key was offered to the store. NEWCORE is testnet-only; the key was not stored."""


class CredentialStoreError(Exception):
    """The store could not be written (bad root, I/O failure). Nothing partial is left behind."""


@runtime_checkable
class CredentialSource(Protocol):
    def api_key(self) -> str: ...

    def sign(self, payload: bytes) -> str: ...


def _check_token(value, what):
    # The value itself is never put into an error message.
    if type(value) is not str or not value:
        raise ValueError(f'{what} must be a non-empty str')
    if not value.isascii() or not value.isprintable() or any(c.isspace() for c in value):
        raise ValueError(f'{what} must be printable ASCII without whitespace')
    return value


def key_digest(api_key):
    """One-way fingerprint of an API key: 'zbk1:' + sha256(domain || key) hex. Stable across runs and machines, so a
    journal can record which credential was in use without ever storing the key."""
    _check_token(api_key, 'api_key')
    return KEY_DIGEST_PREFIX + hashlib.sha256(_DIGEST_DOMAIN + api_key.encode('ascii')).hexdigest()


def mask_key(api_key):
    """'abcd…wxyz' (first and last four). Short keys are fully masked."""
    if type(api_key) is not str or len(api_key) < 12:
        return '…'
    return f'{api_key[:4]}…{api_key[-4:]}'


class StaticCredentials:
    """In-memory key + signer. repr/str are redacted; pickling and copying are refused; equality is identity.
    The raw secret is NOT retained (Cowork finding 5): only a pre-keyed HMAC-SHA256 state is kept, and each sign()
    works on a copy of it, so no attribute (mangled or not) holds the secret string or bytes."""
    __slots__ = ('__key', '__mac')

    def __init__(self, api_key, secret):
        _check_token(api_key, 'api_key')
        _check_token(secret, 'secret')
        self.__key = api_key
        self.__mac = hmac.new(secret.encode('ascii'), digestmod=hashlib.sha256)

    def api_key(self):
        return self.__key

    def sign(self, payload):
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError('payload must be bytes')
        mac = self.__mac.copy()
        mac.update(bytes(payload))
        return mac.hexdigest()

    def key_digest(self):
        return key_digest(self.__key)

    def __repr__(self):
        return f'{type(self).__name__}(api_key={_REDACTED}, secret={_REDACTED})'

    __str__ = __repr__

    def __format__(self, spec):
        return repr(self)

    def __reduce_ex__(self, protocol):
        raise TypeError('credentials cannot be pickled or copied')

    def __reduce__(self):
        raise TypeError('credentials cannot be pickled or copied')

    def __copy__(self):
        raise TypeError('credentials cannot be pickled or copied')

    def __deepcopy__(self, memo):
        raise TypeError('credentials cannot be pickled or copied')

    def __getstate__(self):
        raise TypeError('credentials cannot be pickled or copied')


class StoredCredentials(StaticCredentials):
    """A CredentialSource loaded from the store, carrying the AccountId and environment it is bound to."""
    __slots__ = ('account_id', 'environment', 'created_ms')

    def __init__(self, api_key, secret, *, account_id, environment, created_ms):
        super().__init__(api_key, secret)
        self.account_id, self.environment, self.created_ms = account_id, environment, created_ms

    def __repr__(self):
        return (f'StoredCredentials(account_id={self.account_id!r}, environment={self.environment!r}, '
                f'api_key={_REDACTED}, secret={_REDACTED})')

    __str__ = __repr__


# ---------------------------------------------------------------------------------------------------------------------
# DPAPI (CurrentUser) through ctypes. Imported lazily so the module loads anywhere and touches nothing at import.
# ---------------------------------------------------------------------------------------------------------------------

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DpapiProtector:
    """CryptProtectData / CryptUnprotectData, CurrentUser scope (no LOCAL_MACHINE flag), UI forbidden."""

    @staticmethod
    def available():
        return os.name == 'nt'

    def _api(self):
        if not self.available():
            raise CredentialsUnavailable('DPAPI is only available on Windows', 'unsupported_platform')
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

        crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
        crypt32.CryptProtectData.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.POINTER(Blob),
                                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                                               ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        return ctypes, Blob, crypt32, kernel32

    def _call(self, fn_name, data, entropy):
        ctypes, Blob, crypt32, kernel32 = self._api()
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        ent = ctypes.create_string_buffer(bytes(entropy), len(entropy))
        blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_ent = Blob(len(entropy), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
        blob_out = Blob()
        try:
            if fn_name == 'protect':
                ok = crypt32.CryptProtectData(ctypes.byref(blob_in), 'zackbot-newcore', ctypes.byref(blob_ent),
                                              None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
            else:
                ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, ctypes.byref(blob_ent),
                                                None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
            if not ok:
                return None
            try:
                return ctypes.string_at(blob_out.pbData, blob_out.cbData)
            finally:
                ctypes.memset(blob_out.pbData, 0, blob_out.cbData)
                kernel32.LocalFree(blob_out.pbData)
        finally:
            ctypes.memset(buf, 0, len(data))                  # best effort: drop the plaintext copy we made

    def protect(self, data, entropy):
        out = self._call('protect', data, entropy)
        if out is None:
            raise CredentialStoreError('DPAPI could not protect the record')
        return out

    def unprotect(self, data, entropy):
        out = self._call('unprotect', data, entropy)
        if out is None:
            raise CredentialsUnavailable('the credential file could not be decrypted for this Windows user',
                                         'undecryptable')
        return out


# ---------------------------------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------------------------------

STORE_MAGIC = b'ZBNCKEY\x01'
STORE_VERSION = 1
_ENTROPY_DOMAIN = b'zackbot/newcore/credstore/v1\x00'
ACCOUNT_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_RECORD_FIELDS = {'v', 'environment', 'account_id', 'api_key', 'api_secret', 'key_digest', 'created_ms',
                  'previous_key_digest'}
STORABLE_ENVIRONMENTS = ('testnet',)


@dataclass(frozen=True)
class StoredCredentialInfo:
    """Everything about a stored credential that is safe to show: never the key itself, never the secret."""
    account_id: str
    environment: str
    masked_key: str
    key_digest: str
    created_ms: int
    previous_key_digest: object        # str | None: set when this record replaced a different key (rotation)
    path: str

    @property
    def rotated(self):
        return self.previous_key_digest is not None


def _norm(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _inside(path, root):
    p, r = _norm(path), _norm(root)
    return p == r or p.startswith(r.rstrip('\\/') + os.sep)


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def default_root():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise CredentialsUnavailable('LOCALAPPDATA is not set: no default credential folder', 'not_configured')
    return os.path.join(base, 'ZackBotNC', 'secrets')


def check_root(root):
    """Refuse a credential root inside the legacy data folder (%LOCALAPPDATA%\\ZackBot) or inside the repository."""
    if type(root) is not str or not root:
        raise CredentialStoreError('credential root must be a non-empty path')
    legacy = [os.path.join(v, 'ZackBot') for v in (os.environ.get('LOCALAPPDATA'),
                                                   os.environ.get('ZB_REAL_LOCALAPPDATA')) if v]
    for bad in legacy:
        if _inside(root, bad):
            raise CredentialStoreError('credential root may not be inside the legacy ZackBot data folder')
    if _inside(root, _repo_root()):
        raise CredentialStoreError('credential root may not be inside the repository')
    return root


def restrict_dir_to_current_user(path):
    """Best effort: remove inherited ACEs and grant only the current user (by SID) full control. Returns True when
    icacls reported success. Never raises: DPAPI already binds the blob to this Windows user."""
    if os.name != 'nt':
        return False
    import subprocess
    try:
        who = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True, timeout=20)
        m = re.search(r'"(S-1-[0-9-]+)"', who.stdout or '')
        if who.returncode != 0 or not m:
            return False
        r = subprocess.run(['icacls', path, '/inheritance:r', '/grant:r', f'*{m.group(1)}:(OI)(CI)F'],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class CredentialStore:
    """DPAPI-protected credential file for ONE NEWCORE AccountId."""

    def __init__(self, account_id, *, root=None, protector=None, harden_acl=True):
        if type(account_id) is not str or not ACCOUNT_ID_RE.fullmatch(account_id):
            raise CredentialStoreError('account_id must match ' + ACCOUNT_ID_RE.pattern)
        self.account_id = account_id
        self.root = check_root(default_root() if root is None else root)
        self.protector = protector or DpapiProtector()
        self.harden_acl = harden_acl
        self.acl_restricted = None             # set by save(): True / False (best effort), None = not attempted

    @property
    def path(self):
        return os.path.join(self.root, self.account_id + '.bin')

    def _entropy(self):
        return _ENTROPY_DOMAIN + self.account_id.encode('ascii')

    def __repr__(self):
        return f'CredentialStore(account_id={self.account_id!r}, root={self.root!r})'

    # ---- read ----

    def _read_record(self):
        try:
            with open(self.path, 'rb') as fh:
                blob = fh.read(1 << 20)
        except FileNotFoundError:
            raise CredentialsUnavailable('no stored credentials for this account', 'missing') from None
        except OSError:
            raise CredentialsUnavailable('the credential file could not be read', 'unreadable') from None
        if not blob.startswith(STORE_MAGIC) or len(blob) <= len(STORE_MAGIC):
            raise CredentialsUnavailable('the credential file is not a NEWCORE credential store', 'corrupt')
        try:
            plain = self.protector.unprotect(blob[len(STORE_MAGIC):], self._entropy())
        except CredentialsUnavailable:
            raise
        except Exception:
            raise CredentialsUnavailable('the credential file could not be decrypted', 'undecryptable') from None
        try:
            rec = json.loads(plain.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise CredentialsUnavailable('the decrypted credential record is not valid', 'corrupt') from None
        if not isinstance(rec, dict) or set(rec) != _RECORD_FIELDS or rec.get('v') != STORE_VERSION:
            raise CredentialsUnavailable('the decrypted credential record has an unknown shape', 'corrupt')
        if rec['account_id'] != self.account_id:
            raise CredentialsUnavailable('the credential file belongs to a different NEWCORE account',
                                         'account_mismatch')
        if rec['environment'] not in STORABLE_ENVIRONMENTS:
            raise CredentialsUnavailable('the credential file is not for testnet', 'environment_refused')
        try:
            check_value(_check_token(rec['api_key'], 'api_key'))
            check_value(_check_token(rec['api_secret'], 'api_secret'))
        except ValueError:
            raise CredentialsUnavailable('the decrypted credential record is not valid', 'corrupt') from None
        if rec['key_digest'] != key_digest(rec['api_key']) or type(rec['created_ms']) is not int:
            raise CredentialsUnavailable('the decrypted credential record is inconsistent', 'corrupt')
        return rec

    def load(self, scrubber=None):
        """-> StoredCredentials (a CredentialSource). Raises CredentialsUnavailable(reason) on any problem.
        scrubber: a SecretScrubber to register the key AND secret with, so a caller can redact both without ever
        handling the secret itself."""
        rec = self._read_record()
        if scrubber is not None:
            scrubber.register(rec['api_key'], rec['api_secret'])
        return StoredCredentials(rec['api_key'], rec['api_secret'], account_id=rec['account_id'],
                                 environment=rec['environment'], created_ms=rec['created_ms'])

    def info(self):
        """-> StoredCredentialInfo (masked). Raises CredentialsUnavailable(reason) on any problem."""
        rec = self._read_record()
        return StoredCredentialInfo(rec['account_id'], rec['environment'], mask_key(rec['api_key']),
                                    rec['key_digest'], rec['created_ms'], rec['previous_key_digest'], self.path)

    def exists(self):
        return os.path.exists(self.path)

    # ---- write ----

    def save(self, environment, api_key, api_secret, *, now_ms):
        """Store (or rotate) the credential for this AccountId. Returns StoredCredentialInfo. Mainnet is refused.
        A different key than the stored one is a ROTATION: info.previous_key_digest is set and the engine must treat
        the binding as unconfirmed. Storing never enables trading."""
        if environment == 'mainnet':
            raise MainnetCredentialRefused('NEWCORE is testnet-only: mainnet credentials are not stored')
        if environment not in STORABLE_ENVIRONMENTS:
            raise CredentialStoreError('environment must be "testnet"')
        try:
            check_value(_check_token(api_key, 'api_key'))
            check_value(_check_token(api_secret, 'api_secret'))
        except ValueError as ex:
            raise CredentialStoreError(str(ex)) from None
        if type(now_ms) is not int or now_ms <= 0:
            raise CredentialStoreError('now_ms must be a positive int')
        digest = key_digest(api_key)
        previous = None
        if self.exists():
            try:
                old = self._read_record()
            except CredentialsUnavailable:
                old = None                       # an unreadable old file is replaced; nothing to compare with
            if old is not None and old['key_digest'] != digest:
                previous = old['key_digest']
            elif old is not None:
                previous = old['previous_key_digest']
        rec = {'v': STORE_VERSION, 'environment': environment, 'account_id': self.account_id, 'api_key': api_key,
               'api_secret': api_secret, 'key_digest': digest, 'created_ms': now_ms, 'previous_key_digest': previous}
        plain = json.dumps(rec, sort_keys=True, separators=(',', ':')).encode('utf-8')
        blob = STORE_MAGIC + self.protector.protect(plain, self._entropy())
        self._atomic_write(blob)
        return StoredCredentialInfo(self.account_id, environment, mask_key(api_key), digest, now_ms, previous,
                                    self.path)

    def _atomic_write(self, blob):
        try:
            created = not os.path.isdir(self.root)
            os.makedirs(self.root, exist_ok=True)
            if self.harden_acl and (created or self.acl_restricted is None):
                self.acl_restricted = restrict_dir_to_current_user(self.root)
            tmp = self.path + f'.tmp{os.getpid()}'
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0), 0o600)
            try:
                with os.fdopen(fd, 'wb') as fh:
                    fh.write(blob)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            raise CredentialStoreError('the credential file could not be written') from None

    def clear(self):
        """Remove this account's credential file. Returns True if a file was removed."""
        try:
            os.remove(self.path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            raise CredentialStoreError('the credential file could not be removed') from None


# ---------------------------------------------------------------------------------------------------------------------
# Log / exception scrubber
# ---------------------------------------------------------------------------------------------------------------------

class SecretScrubber:
    """Redacts registered secret values from log records (message, args, exception and stack text) and from any text.
    install() hooks the log-record factory (covers every logger and every handler, including ones added later),
    adds a logging.Filter to every existing handler, and wraps sys.excepthook."""

    def __init__(self):
        self._values = []
        self._installed = None

    def __repr__(self):
        return f'SecretScrubber(values=<{len(self._values)} redacted>)'

    def register(self, *values):
        """Register secret values. A value shorter than redact.MIN_SECRET_LEN (or not a str) is REFUSED with
        ValueError, never silently dropped (Cowork finding 1)."""
        for v in values:
            check_value(v)
        for v in values:
            if v not in self._values:
                self._values.append(v)
        self._values.sort(key=len, reverse=True)

    def redaction_values(self):
        """The registered values, for an in-process redactor/leak check only (e.g. CassetteRecorder(redact=...)).
        Never log or print the result."""
        return tuple(self._values)

    def scrub(self, text):
        """Case-insensitive, percent-encoding and separator tolerant (redact.redact_values)."""
        return redact_values(str(text), self._values)

    def scrub_record(self, record):
        import logging
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg, record.args = self.scrub(msg), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.scrub(record.exc_text)
            record.exc_info = None if self._values else record.exc_info
        if record.stack_info:
            record.stack_info = self.scrub(record.stack_info)
        return record

    def filter(self):
        import logging
        scrubber = self

        class _Filter(logging.Filter):
            def filter(self, record):
                scrubber.scrub_record(record)
                return True
        return _Filter()

    def install(self):
        import logging
        import sys
        if getattr(self, '_installed', None):
            return self._installed['filter']
        old_factory = logging.getLogRecordFactory()

        def factory(*args, **kwargs):
            return self.scrub_record(old_factory(*args, **kwargs))
        logging.setLogRecordFactory(factory)
        flt = self.filter()
        handlers = []
        loggers = [logging.getLogger()] + [lg for lg in logging.Logger.manager.loggerDict.values()
                                           if isinstance(lg, logging.Logger)]
        for lg in loggers:
            for h in lg.handlers:
                h.addFilter(flt)
                handlers.append(h)
        old_hook = sys.excepthook

        def hook(exc_type, exc, tb):
            import traceback
            sys.stderr.write(self.scrub(''.join(traceback.format_exception(exc_type, exc, tb))))
        sys.excepthook = hook
        self._installed = dict(filter=flt, factory=factory, old_factory=old_factory, handlers=handlers,
                               hook=hook, old_hook=old_hook)
        return flt

    def uninstall(self):
        """Undo install() (tests; a long-running process keeps it installed)."""
        import logging
        import sys
        st = getattr(self, '_installed', None)
        if not st:
            return
        if logging.getLogRecordFactory() is st['factory']:
            logging.setLogRecordFactory(st['old_factory'])
        for h in st['handlers']:
            h.removeFilter(st['filter'])
        if sys.excepthook is st['hook']:
            sys.excepthook = st['old_hook']
        self._installed = None
