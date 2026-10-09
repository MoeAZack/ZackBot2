"""Exception text never reaches incidents, logs or CLI output raw (Codex #13 P2, 78c0010).

An exception from OUTSIDE our code (a venue adapter, a transport, the OS, a store backend) may carry a URL, a header or a
key fragment. It is recorded as its type plus a bounded correlation tag only: an HMAC over the type and args under a
per-process random salt, 10 hex chars, so equal errors correlate within one run, nothing can be read back from it,
and it cannot be brute-forced offline (the salt never leaves the process). Messages composed by OUR validators
(NC-01 DomainError / InvalidRecord, run-config / store refusals naming a field or path, never a credential value) stay
readable: pass them as `ours`.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from newcore.domain.errors import DomainError

_SALT = secrets.token_bytes(16)
TAG_HEX = 10


def exc_tag(ex):
    """'<Type>#<tag>': never the message."""
    body = (type(ex).__qualname__ + '\x00' + repr(getattr(ex, 'args', ()))).encode('utf-8', 'replace')
    return f'{type(ex).__name__}#{hmac.new(_SALT, body, hashlib.sha256).hexdigest()[:TAG_HEX]}'


def describe(ex, ours=()):
    """Our own validation errors keep their (bounded) message; anything else is exc_tag only."""
    if isinstance(ex, (DomainError, *ours)):
        return f'{type(ex).__name__}: {str(ex)[:300]}'
    return exc_tag(ex)
