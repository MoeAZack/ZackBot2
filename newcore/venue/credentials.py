"""Credential source interface for the testnet transport.

Owner decision: NEWCORE uses a separate testnet account and key that the OWNER enters; Claude never handles it. So the
transport only ever sees an object implementing CredentialSource:

    api_key() -> str          the X-MBX-APIKEY header value (sent on SIGNED requests only)
    sign(payload: bytes) -> str   lower-case hex HMAC-SHA256 of payload under the secret

The secret never has to leave the source. StaticCredentials is the simple in-memory implementation (tests, and the
later owner-run loader); it redacts itself everywhere and refuses to be pickled or copied.
key_digest() is a one-way, domain-separated fingerprint of the API key. It identifies a CREDENTIAL, not an exchange
account (Codex ruling 2026-10-08): the NEWCORE AccountId binding is provisioned before the first INIT, and a rotated
key keeps the AccountId but leaves the binding unconfirmed (HOLD + reconcile + confirm).
Nothing here needs credentials at import time; fake / replay / S1-S4 run without any.
"""
import hashlib
from typing import Protocol, runtime_checkable

from .signing import hmac_sha256_hex

KEY_DIGEST_PREFIX = 'zbk1:'
_DIGEST_DOMAIN = b'zackbot/newcore/venue/key-digest/v1\x00'
_REDACTED = '<redacted>'


class CredentialsUnavailable(Exception):
    """No usable credentials (none configured, store missing / corrupt / undecryptable, source failure). A clean typed
    failure: nothing was sent. The engine treats it as idle/HOLD; it is never retried in a loop here."""


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
    journal can record which key an account was bound to without ever storing the key."""
    _check_token(api_key, 'api_key')
    return KEY_DIGEST_PREFIX + hashlib.sha256(_DIGEST_DOMAIN + api_key.encode('ascii')).hexdigest()


class StaticCredentials:
    """In-memory key + secret. repr/str are redacted; pickling and copying are refused; equality is identity."""
    __slots__ = ('__key', '__secret')

    def __init__(self, api_key, secret):
        _check_token(api_key, 'api_key')
        _check_token(secret, 'secret')
        self.__key = api_key
        self.__secret = secret.encode('ascii')

    def api_key(self):
        return self.__key

    def sign(self, payload):
        return hmac_sha256_hex(self.__secret, payload)

    def key_digest(self):
        return key_digest(self.__key)

    def __repr__(self):
        return f'StaticCredentials(api_key={_REDACTED}, secret={_REDACTED})'

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
