"""Shared redaction rules (Cowork findings 1 and 3): used by VenueError messages, the SecretScrubber and the cassette.

Two independent nets, both fail-closed by design:
- BY NAME: a field / parameter / header whose name, after percent-decoding, lower-casing and dropping '-', '_', '.'
  and spaces, contains a sensitive fragment (listenKey, signature, apiKey / X-MBX-APIKEY, secret, token, cookie /
  set-cookie, password, authorization, private key) never keeps its value.
- BY VALUE: a registered secret value is found case-insensitively, percent-encoded (quote / quote_plus, either hex
  case) and with '-', '_' or spaces inserted between its characters, and replaced.
A registered value shorter than MIN_SECRET_LEN is REFUSED (ValueError), never silently dropped: a short value cannot be
redacted safely, and silently skipping it was exactly the leak.
"""
import re
import unicodedata
import urllib.parse

REDACTED = '<redacted>'
MIN_SECRET_LEN = 8
SENSITIVE_FRAGMENTS = ('listenkey', 'signature', 'apikey', 'secret', 'token', 'cookie', 'password', 'passwd',
                       'authorization', 'privatekey')
_SEP = '[-_ ]?'
# Long token-like runs, separators included (finding 3: a secret split by '_' or '-' must not slip through).
TOKEN_RUN = re.compile(r'[A-Za-z0-9_\-+/=]{32,}')


def normalize_name(name):
    """Percent-decoded, NFKC-normalised (fullwidth 'ｓｉｇｎａｔｕｒｅ' -> 'signature'), case-folded, and every
    character that is not a letter or digit dropped (separators, zero-width and format characters included)."""
    n = unicodedata.normalize('NFKC', urllib.parse.unquote_plus(str(name))).casefold()
    return ''.join(ch for ch in n if ch.isalnum())


def is_sensitive_name(name):
    n = normalize_name(name)
    return any(f in n for f in SENSITIVE_FRAGMENTS)


def check_value(value):
    if not isinstance(value, str) or len(value) < MIN_SECRET_LEN:
        raise ValueError(f'a redaction value must be a str of at least {MIN_SECRET_LEN} characters')
    return value


def value_pattern(value):
    """One case-insensitive regex for the value in all its known disguises."""
    check_value(value)
    variants = {value, urllib.parse.quote(value, safe=''), urllib.parse.quote_plus(value),
                urllib.parse.quote(value, safe='/')}
    alts = []
    for v in sorted(variants, key=len, reverse=True):
        # Percent escapes stay atomic; separators may appear between any two other characters.
        parts = re.findall(r'%[0-9A-Fa-f]{2}|.', v, flags=re.S)
        alts.append(_SEP.join(re.escape(p) for p in parts))
    return re.compile('|'.join(alts), re.IGNORECASE)


class PatternCache:
    """Compiled value_pattern per value, owned by ONE redactor (a CassetteRecorder, a SecretScrubber): compiling
    the separator-tolerant pattern dominated recording time (a pattern per value per text). Not module-global, so
    secret-derived patterns live exactly as long as their owner. Values are checked like value_pattern does."""

    def __init__(self):
        self._p = {}

    def get(self, value):
        p = self._p.get(value)
        if p is None:
            p = self._p[value] = value_pattern(value)
        return p

    def __repr__(self):
        return f'PatternCache(<{len(self._p)}>)'


def redact_values(text, values, cache=None):
    text = str(text)
    pattern = value_pattern if cache is None else cache.get
    for v in sorted(values, key=len, reverse=True):
        text = pattern(v).sub(REDACTED, text)
    return text


def contains_values(text, values, cache=None):
    """True if any value is present in text, or in its percent-decoded form."""
    text = str(text)
    forms = (text, urllib.parse.unquote(text), urllib.parse.unquote_plus(text))
    for v in values:
        p = value_pattern(v) if cache is None else cache.get(v)
        if any(p.search(f) for f in forms):
            return True
    return False


def scrub_tokens(text):
    return TOKEN_RUN.sub(REDACTED, str(text))
