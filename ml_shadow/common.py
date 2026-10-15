"""Shared helpers: schema version, canonical JSON, sha256, secret-name redaction."""
import hashlib, json, math, re

SCHEMA_VERSION = 'ml_shadow.v1'
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
