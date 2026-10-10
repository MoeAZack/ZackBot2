"""Exception text that is safe to put in a report, an incident, a cassette or console output (Codex, nc-tnet01-next).

A raw `str(ex)` / `repr(ex)` / f'{ex}' can carry whatever the raiser put in it: a request URL, a header, a path, a
fragment of a key. So exception text leaves the venue lane, the TNET harness and the tools ONLY through this module:

- an exception class in SAFE_MESSAGES keeps its message (long token runs scrubbed except our own zbn1 client ids,
  at most `limit` characters). A class is listed only after EVERY one of its raise sites was checked to build the
  message from constants, internal numbers, validated ids or enumerated reasons - never from a response body, a URL,
  a header, a path or raw user input. The match is on the exact class (module + name): a subclass is NOT covered;
- every other exception is a fixed template: its type name, an errno for OSError, and a correlation tag (exc_ref:
  an HMAC under a per-process random salt that is never written anywhere, 10 hex - the same failure gives the same
  tag within one process; nothing of the text can be read back or brute-forced from it).

exc_msg(ex)  -> the message alone        (where a line used to print f'{ex}')
exc_text(ex) -> 'Name: message'          (where a line used to print f'{type(ex).__name__}: {ex}')
Both give the same template for an unlisted exception. tests/newcore_venue/test_ncv_safe_text.py checks that every
listed class exists and bans the raw forms in newcore/venue, newcore/tnet and tools.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from newcore.venue.redact import REDACTED, TOKEN_RUN

LIMIT = 200
OWN_CLIENT_ID = re.compile(r'zbn1[oae]-[a-z2-7]{26}')          # = newcore.venue.tnet.NEWCORE_CID_RE (pinned by a test)

SAFE_MESSAGES = frozenset({
    ('newcore.tnet.seams', 'BoundExceeded'),            # cycle / order / notional bounds: numbers + our client ids
    ('newcore.tnet.seams', 'DeadlineExceeded'),         # deadlines: numbers + our client ids
    ('newcore.venue.tnet_seams', 'DeadlineExceeded'),   # run deadline: numbers + our client ids
    ('newcore.venue.tnet_probes', 'ProbeAborted'),      # constants, Binance error CODE (int), our client ids
    ('newcore.venue.run_config', 'RunConfigError'),     # constants (no key path, no value)
    ('newcore.venue.tnet_spec', 'SpecError'),           # spec path + constant reason / json's constant msg
    ('newcore.venue.credentials', 'CredentialsUnavailable'),     # constants + enumerated reason
    ('newcore.venue.credentials', 'MainnetCredentialRefused'),   # constant
    ('newcore.venue.credentials', 'CredentialStoreError'),       # constants (no wrapped text)
    ('newcore.venue.factory', 'BindingMismatch'),       # account id + 16-hex binding digests (shareable by design)
    ('newcore.venue.smoke_trade', 'TradeAborted'),      # constants, validated symbol, read KIND, our client ids
    ('newcore.venue.testnet_venue', 'HedgeModeRequired'),        # constants
    ('newcore.venue.testnet_venue', 'VenueBootUnknown'),         # constant + read KIND
    ('newcore.venue.cassette', 'CassetteLeak'),         # constants
    ('newcore.venue.tnet', 'ReportLeak'),               # constants + a config field NAME (never its value)
    ('newcore.runner.runner', 'InvariantBreach'),       # S1: 'I1 SYMBOL SIDE: position q, confirmed stop q' /
                                                        # 'I2 ...' constants (re-vet when S1 changes them)
})


def _message(ex):
    try:
        return str(ex)
    except Exception:                               # noqa: BLE001 - a broken __str__ is still reported typed
        return '<unprintable>'


def is_safe(ex):
    return (type(ex).__module__, type(ex).__qualname__) in SAFE_MESSAGES


# The correlation tag: THE SAME primitive as S1's newcore/runner/redact.py::exc_tag (Codex #13 P2 on 78c0010 and
# 6069136564 here) - an HMAC-SHA256 over the type and args under a per-process random salt, TAG_HEX hex chars. The salt
# is never written anywhere (never logged, reported, recorded or returned), so a tag cannot be brute-forced offline
# by dictionary (a password, a path, a symbol), and 40 bits do not collide in practice. Equal errors correlate
# within one process only. The venue lane cannot import newcore.runner (it does not exist on nc-venue-testnet), hence
# the copy; keep the two in step (body = qualname NUL repr(args), TAG_HEX = 10).
_SALT = secrets.token_bytes(16)
TAG_HEX = 10


def exc_ref(ex):
    """A bounded correlation tag for the exception (HMAC-SHA256 under the per-process salt, TAG_HEX hex chars)."""
    try:
        args = repr(getattr(ex, 'args', ()))
    except Exception:                               # noqa: BLE001 - a broken __repr__ still gets a tag
        args = '<unprintable>'
    body = (type(ex).__qualname__ + '\x00' + args).encode('utf-8', 'replace')
    return hmac.new(_SALT, body, hashlib.sha256).hexdigest()[:TAG_HEX]


def _template(ex):
    errno = getattr(ex, 'errno', None) if isinstance(ex, OSError) else None
    tail = f' errno {errno}' if type(errno) is int else ''
    return f'{type(ex).__name__}{tail} (detail withheld, ref {exc_ref(ex)})'


def _scrub(text):
    return TOKEN_RUN.sub(lambda m: m.group(0) if OWN_CLIENT_ID.fullmatch(m.group(0)) else REDACTED, text)


def exc_msg(ex, limit=LIMIT):
    """The message of a SAFE_MESSAGES class (scrubbed, capped); otherwise the fixed template."""
    return _scrub(_message(ex))[:limit] if is_safe(ex) else _template(ex)


def exc_text(ex, limit=LIMIT):
    """'Name: message' for a SAFE_MESSAGES class (scrubbed, capped); otherwise the fixed template."""
    return f'{type(ex).__name__}: {_scrub(_message(ex))}'[:limit] if is_safe(ex) else _template(ex)
