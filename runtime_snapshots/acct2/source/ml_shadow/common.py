"""Shared helpers: schema version, canonical JSON, sha256, secret-name redaction."""
import hashlib, json, math, re
from datetime import datetime

import numpy as np
import pandas as pd

SCHEMA_VERSION = 'ml_shadow.v2'   # v2 (hook): decision rows carry status taken|skipped|pending
STATUSES = ('taken', 'skipped', 'pending')   # pending = resting maker / armed trailing entry; label accepted if it fills
# Any field NAME matching this is dropped before a row is written - at any nesting depth.
SECRET_NAME_RE = re.compile(r'(key|secret|token|passw|passphrase|signature|credential|auth|cookie|bearer)', re.I)


def _clean(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def redact(obj):
    """Recursively drop dict entries whose key looks like a secret; non-finite floats become None."""
    if isinstance(obj, dict):
        return {str(k): redact(v) for k, v in obj.items() if not SECRET_NAME_RE.search(str(k))}
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    return _clean(obj)


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False, default=str)


def sha256_hex(s):
    if not isinstance(s, (bytes, bytearray)):
        s = str(s).encode('utf-8')
    return hashlib.sha256(s).hexdigest()


def to_utc(t):
    """The ONE time normaliser. Accepts datetime / pd.Timestamp / np.datetime64 / ISO string (naive = UTC) and
    integers, which are ALWAYS epoch MILLISECONDS. Anything else (floats, bools, None, junk) raises TypeError/ValueError;
    the hooks turn that into a None return, never an exception into the engine."""
    if isinstance(t, (bool, np.bool_)):
        raise TypeError('bool is not a time')
    if isinstance(t, (int, np.integer)):
        ts = pd.Timestamp(int(t), unit='ms')
    elif isinstance(t, (pd.Timestamp, datetime, np.datetime64, str)):
        ts = pd.Timestamp(t)
    else:
        raise TypeError(f'unsupported time type {type(t).__name__}')
    if ts is pd.NaT or pd.isna(ts):
        raise ValueError('NaT')
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


REASON_MAX = 200
_TEXT_SECRET_RES = (   # order matters: Bearer first, so 'Authorization: Bearer <tok>' loses the token (Cowork F3)
    re.compile(r'(?i)\bbearer\s+\S+'),
    re.compile(r'(?i)\b[\w-]*(?:key|secret|token|passw|passphrase|sig|auth|cookie|credential)[\w-]*\s*[=:]\s*\S+'),
    re.compile(r'(?i)\b(?:sk|pk|rk|wrkspc|acct|org|user|key|tok)[-_][\w-]{4,}'),            # prefixed ids/keys
    re.compile(r'(?i)\b(?:account|acct|order|client|user|workspace|org)[\s_-]?(?:id|no|num(?:ber)?)?\s*[=:#]\s*\S+'),
    re.compile(r'\d{8,}'),                                                                       # long numeric ids
    re.compile(r'[A-Za-z0-9_\-+/=.]{32,}'),
)


def redact_text(s, cap=REASON_MAX):
    """Conservative free-text redaction: drop key=/secret:/token=... pairs, Bearer credentials and long unseparated
    tokens (>= 32 chars), then cap the length."""
    s = '' if s is None else str(s)
    for rx in _TEXT_SECRET_RES:
        s = rx.sub('[REDACTED]', s)
    return s[:cap]


def corr_id_of(trade_id):
    """Correlation id persisted for a taken trade: an unsalted sha256 prefix of the raw trade/order id, so raw
    exchange order ids never reach disk but the engine can still recompute and join."""
    return None if trade_id is None else 'c_' + sha256_hex(str(trade_id))[:16]
