"""Cassette seam for S5: record sanitized request/response pairs from a live run, replay them as the injected `http`.

Recording (owner-run S5 smoke):  http = CassetteRecorder(TestnetHttpSender(), redact=(...))
Replay (CI, no network):         http = CassettePlayer('cassette.json')

Sanitizing (Cowork finding 1: by NAME and by VALUE, then a fail-closed audit; rules in redact.py):
- BY NAME: a query parameter, request/response header, JSON field or form-style `name=value` whose name is sensitive
  (listenKey, signature, apiKey / X-MBX-APIKEY, secret, token, cookie / Set-Cookie, password, authorization, ...),
  matched after percent-decoding and case/separator folding (so `Signature`, `%73ignature`, `LISTEN_KEY` count), has
  its value replaced by <redacted>. Every value blanked this way (8+ chars) is also LEARNED as a secret value.
- BY VALUE: the API key from the wire header, every signature, every learned value and every `redact=` value is
  replaced wherever it appears, case-insensitively, percent-encoded and with separators inserted. A `redact=` value
  shorter than 8 characters is refused (ValueError), never dropped.
- AUDIT (to_json, fail closed): the serialized cassette, its percent-decoded form and every base64 body are checked
  again for every value (every string and key of the completed document, the _provenance note included, also in
  decoded and generated base64 forms, within one total work budget), and every query pair, header, JSON field
  (parsed, any depth) and form pair is checked for a sensitive name that still carries a value. Anything left
  raises CassetteLeak and nothing is produced.

Replay matches interactions in order on method, URL, the signed flag and every query parameter, except that the
volatile `timestamp` and the sensitive parameters (signature, listenKey, ...) are compared by presence only (a
replay signs with its own clock and dummy key). A signed replay request must still carry an API-key header and a
signature. A mismatch or an exhausted cassette raises CassetteMismatch, a WireSeamError the transport re-raises.
"""
import base64
import binascii
import json
import os
import re
import unicodedata
import urllib.parse
import warnings

from . import cassette_allow as A

from .redact import (MIN_SECRET_LEN, REDACTED, TOKEN_RUN, PatternCache, check_value, contains_values,
                     is_sensitive_name,
                     redact_values)
from .wire import (API_KEY_HEADER, HttpResponse, WireConnectionError, WireNotSent, WireResponseTooLarge, WireSeamError,
                   WireTimeout)

CASSETTE_FORMAT = 'zb-newcore-cassette/1'
VOLATILE_PARAMS = ('timestamp',)
_ERRORS = {'WireTimeout': WireTimeout, 'WireNotSent': WireNotSent, 'WireResponseTooLarge': WireResponseTooLarge,
           'WireConnectionError': WireConnectionError}
_CLEARED = (REDACTED, '', None)
MAX_RECORD_BODY = 8 * 1024 * 1024           # a larger answer is not stored (the cassette becomes unproducible)
_REASON_RE = re.compile(r'[a-z_]{1,20}')
# Codex P1 on bc5a351: decode runs down to the shortest encoding of a MIN_SECRET_LEN-byte secret (8 bytes -> 11
# characters unpadded), so a case-variant of a short secret is decoded and matched case-insensitively.
_B64_MIN = -(-MIN_SECRET_LEN * 8 // 6)
_B64_RUN = re.compile(r'[A-Za-z0-9+/_-]{%d,}={0,2}' % _B64_MIN)
_HEX_RUN = re.compile(r'[0-9a-fA-F]{%d,}' % (2 * MIN_SECRET_LEN))
_LINE_BREAK = re.compile(r'[\r\n]+')
_PCT = re.compile(r'%[0-9A-Fa-f]{2}')
MAX_DECODED_TOKENS = 50_000               # encoded runs decoded per string (beyond: fail closed)
DECODE_DEPTH = 2                          # nested encodings decoded (base64 of base64, base64 of hex, ...)
MAX_SURFACES = 24                         # rewritten forms (percent / NFKC / unicode-escape / unwrapped) per text
# Totals over the WHOLE audit (Codex P2s on 1297ee3 / bcc2d8d). Units are UTF-8 BYTES, and the document is charged as a
# structure (every container, key and scalar, at a conservative serialized size) before it is serialized at all.
MAX_AUDIT_RUNS = 4_000_000                # decode attempts (each run x byte offset x alphabet, at every depth)
MAX_AUDIT_STRINGS = 1_000_000             # string leaves + object keys
MAX_AUDIT_NODES = 4_000_000               # every node: containers, keys, strings, numbers, booleans, nulls
MAX_AUDIT_CHARS = 128 * 1024 * 1024       # bytes of the document (conservative serialized size)
MAX_AUDIT_DERIVED = 16 * MAX_AUDIT_CHARS  # bytes of every rewritten / decoded view the audit produces
MAX_SECRET_BYTES = 4096                   # one registered / learned secret value, UTF-8 bytes (Codex P2 on bc5a351)
MAX_SECRET_TOTAL_BYTES = 512 * 1024       # all secret values together, checked when each is REGISTERED
_URLSAFE = str.maketrans('+/', '-_')
MAX_LEARNED_VALUES = 4096                  # distinct secret values one recorder will scrub (beyond: fail closed)
# Segmented evidence (Codex T04 triage 6087020052): T04-algo (31 cycles; one account-wide positionRisk read alone costs
# ~96,000 decode attempts) passes MAX_AUDIT_RUNS as ONE document, so its cassette was refused at save. to_segments() closes
# a segment at a scenario checkpoint (a cycle boundary) once it holds SEGMENT_CHARS; every segment is a complete
# cassette audited on its own under the UNCHANGED per-audit budget, all before anything is produced. The WHOLE cassette
# stays under MAX_AUDIT_CHARS and at most MAX_SEGMENTS segments are audited (total work bounded).
SEGMENT_CHARS = MAX_AUDIT_CHARS // 8       # conservative serialized bytes after which a segment closes (16 MiB)
MAX_SEGMENTS = 16


def _strings(obj):
    """Every string leaf (iterative: no recursion limit)."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            stack.extend(o.values())
        elif isinstance(o, (list, tuple)):
            stack.extend(o)


def _strings_and_keys(obj):
    """Every string leaf and every object key (iterative)."""
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str):
                    yield k
                stack.append(v)
        elif isinstance(o, (list, tuple)):
            stack.extend(o)


_ESCAPED = dict.fromkeys([*range(32), ord('"'), ord('\\')])      # str.translate: delete them


def _utf8_len(s):
    return len(s.encode('utf-8', 'surrogatepass'))


class _AuditBudget:
    """The total work of one cassette audit (Codex P2s on 1297ee3 / bcc2d8d): the per-string cap alone let many small
    strings or many interactions create unbounded work. Exceeding any total is a refusal (fail closed), never a skip."""

    def __init__(self):
        self.runs = self.strings = self.nodes = self.chars = self.derived = 0

    def _text(self):
        raise CassetteLeak('too much text to audit; cassette not produced')

    def document(self, doc):
        """Charge the whole structure BEFORE it is serialized or scanned: one node per container / key / scalar and
        its conservative serialized size in UTF-8 bytes (iterative: no recursion limit)."""
        stack = [doc]
        while stack:
            o = stack.pop()
            self.nodes += 1
            if self.nodes > MAX_AUDIT_NODES:
                self._text()
            if isinstance(o, str):
                self.string(o)
            elif isinstance(o, dict):
                self.chars += 2
                for k, v in o.items():
                    self.nodes += 1
                    self.chars += 4
                    if isinstance(k, str):
                        self.string(k)
                    stack.append(v)
            elif isinstance(o, (list, tuple)):
                self.chars += 2 + 2 * len(o)
                stack.extend(o)
            elif isinstance(o, bool) or o is None:
                self.chars += 5
            elif isinstance(o, int):
                self.chars += 2 + o.bit_length() // 3      # decimal digits without converting a huge int
            else:
                self.chars += 32
            if self.chars > MAX_AUDIT_CHARS:
                self._text()

    def string(self, s):
        self.strings += 1
        # serialized: UTF-8 bytes + quotes, and up to 5 more bytes per escaped character (\u0000 for a control)
        self.chars += _utf8_len(s) + 2 + 5 * (len(s) - len(s.translate(_ESCAPED)))
        if self.strings > MAX_AUDIT_STRINGS or self.chars > MAX_AUDIT_CHARS:
            self._text()

    def view(self, s):
        self.derived += len(s)
        if self.derived > MAX_AUDIT_DERIVED:
            raise CassetteLeak('too much decoded text to audit; cassette not produced')

    def run(self):
        self.runs += 1
        if self.runs > MAX_AUDIT_RUNS:
            raise CassetteLeak('too many encoded runs to audit; cassette not produced')


def secret_cost(value):
    """UTF-8 bytes of a secret value, refused (ValueError) above MAX_SECRET_BYTES."""
    n = _utf8_len(value)
    if n > MAX_SECRET_BYTES:
        raise ValueError(f'a redaction value must be at most {MAX_SECRET_BYTES} UTF-8 bytes')
    return n


def b64_forms(values):
    """Every base64 form of every registered secret, generated FROM the secret (Codex P1 on 1297ee3: _B64_RUN needs
    16+ characters, so the 11-12 character encoding of an 8-11 byte secret was never decoded). For each of the three
    byte alignments inside a larger blob, the characters whose six bits all come from the secret; standard and
    URL-safe alphabets. Any base64 text holding the secret (alone, padded or not, or inside a blob) contains one."""
    forms = set()
    for v in values:
        for enc in ('utf-8', 'latin-1'):
            try:
                b = v.encode(enc)
            except UnicodeEncodeError:
                continue
            for k in range(3):
                full = base64.b64encode(b'\0' * k + b).decode('ascii')
                stable = full[-(-8 * k // 6):(8 * (k + len(b))) // 6]
                if stable:
                    forms.add(stable)
                    forms.add(stable.translate(_URLSAFE))
    return forms


def _rewrites(text, nfkc):
    """One-step rewrites a value or a run could hide behind: percent-decoding (plain and form), NFKC, one level of
    unicode-escape decoding, and MIME-style line wrapping removed. Each applies only when it can change the text.
    nfkc=False inside decoded bytes (latin-1 text: NFKC there cannot restore a secret, it only costs)."""
    if '%' in text and _PCT.search(text):     # a bare '+' needs no rewrite: value_pattern has the quote_plus form
        yield urllib.parse.unquote(text)
        yield urllib.parse.unquote_plus(text)
    if nfkc and not text.isascii():
        yield unicodedata.normalize('NFKC', text)
    if '\\' in text:
        try:
            with warnings.catch_warnings():        # an unknown escape (\w) is kept as is, not a warning
                warnings.simplefilter('ignore', DeprecationWarning)
                unescaped = text.encode('latin-1', 'backslashreplace').decode('unicode_escape')
        except (UnicodeDecodeError, ValueError):
            unescaped = None
        if unescaped is not None:
            yield unescaped
    if '\n' in text or '\r' in text:
        yield _LINE_BREAK.sub('', text)


def _surfaces(text, budget, nfkc=True):
    """The text and every composition of _rewrites reachable from it (percent inside escapes inside NFKC, ...),
    deduplicated; at most MAX_SURFACES (beyond: fail closed)."""
    seen = {text}
    out = [text]
    i = 0
    while i < len(out):
        for r in _rewrites(out[i], nfkc):
            if r not in seen:
                if len(out) >= MAX_SURFACES:
                    raise CassetteLeak('too many encoded forms to audit; cassette not produced')
                if budget is not None:
                    budget.view(r)
                seen.add(r)
                out.append(r)
        i += 1
    return out


def _decodes(surface, offsets):
    """(run index, decoder, token) for every base64 run at each of `offsets` character offsets (a run glued to other
    base64 characters loses its alignment; Cowork LOW-1 on bcc2d8d), in both alphabets, padded or not; and every hex
    run at both offsets."""
    n = 0
    for m in _B64_RUN.finditer(surface):
        n += 1
        run = m.group(0).rstrip('=')
        for k in range(offsets):
            tok = run[k:]
            if len(tok) < _B64_MIN:
                break
            if len(tok) % 4 == 1:              # a dangling character cannot decode; the rest still can
                tok = tok[:-1]
            pad = '=' * (-len(tok) % 4)
            for fn in (base64.b64decode, base64.urlsafe_b64decode):
                yield n, fn, tok + pad
    for m in _HEX_RUN.finditer(surface):
        n += 1
        run = m.group(0)
        for k in range(2):
            tok = run[k:]
            yield n, bytes.fromhex, tok[:len(tok) - len(tok) % 2]


def decoded_views(text, budget=None, *, _depth=DECODE_DEPTH, _top=True):
    """Text forms a registered secret could hide in (Cowork #37 R2, Codex P1 on bcc2d8d): every surface of the text
    (itself and every composition of percent-decoding, NFKC, unicode-escape and unwrapping) and, on EVERY surface,
    every base64 / hex run decoded, recursively DECODE_DEPTH levels deep (all four offsets at the top level, the
    aligned one inside a decoded view), each decoded view again with its surfaces. The caller matches each view
    case-insensitively; percent-decoded forms are already among the views. Bounded: at most MAX_DECODED_TOKENS runs
    per surface, every decode attempt and every view charged to the budget."""
    for surface in _surfaces(text, budget, nfkc=_top):
        yield surface
        if _depth == 0:
            continue
        for n, fn, tok in _decodes(surface, 4 if _top else 1):
            if n > MAX_DECODED_TOKENS:         # NEW-R2a: never stop auditing early; too many runs is a refusal
                raise CassetteLeak('too many encoded runs to audit; cassette not produced')
            if budget is not None:
                budget.run()
            try:
                view = fn(tok).decode('latin-1')
            except (binascii.Error, ValueError):
                continue
            if budget is not None:
                budget.view(view)
            if len(view) >= MIN_SECRET_LEN:
                yield from decoded_views(view, budget, _depth=_depth - 1, _top=False)


# "key": value  (string, number, bool, null) -- value is replaced only when the key is sensitive.
_JSON_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"(\s*:\s*)("(?:[^"\\]|\\.)*"|-?[0-9][0-9.eE+-]*|true|false|null)')
# name=value in query strings, form bodies, cookies and free text.
# Unicode names too (R3): \w covers fullwidth / other scripts; soft hyphen and zero-width characters join a name
_NAME_CH = r'[\w%.\-­​-‏⁠﻿]'
_FORM_PAIR = re.compile(r'(?<!' + _NAME_CH + r')(' + _NAME_CH + r'+)=([^&\s;,"\'<>]*)')


class CassetteMismatch(WireSeamError):
    """The replayed request does not match the next recorded interaction (or the cassette is exhausted)."""


class CassetteLeak(WireSeamError):
    """A secret value or a sensitive field value is still present in the cassette; nothing was produced."""


def _error_name(ex):
    for name in ('WireTimeout', 'WireNotSent', 'WireResponseTooLarge', 'WireConnectionError'):
        if isinstance(ex, _ERRORS[name]):
            return name
    return None


def _json_text(raw):
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def _sensitive_json_leftovers(value, path='$'):
    """Paths in a parsed JSON value where a sensitive key still holds something other than <redacted>/''/null."""
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            if is_sensitive_name(k) and v not in _CLEARED:
                out.append(f'{path}.{k}')
            out += _sensitive_json_leftovers(v, f'{path}.{k}')
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out += _sensitive_json_leftovers(v, f'{path}[{i}]')
    return out


def _sensitive_text_leftovers(text):
    """Sensitive names still carrying a value in raw text (JSON-like pairs or name=value pairs)."""
    bad = []
    for m in _JSON_PAIR.finditer(text):
        v = m.group(3)
        if is_sensitive_name(_json_text(m.group(1))) and v not in ('null', '""', f'"{REDACTED}"'):
            bad.append(m.group(1))
    for m in _FORM_PAIR.finditer(text):
        if is_sensitive_name(m.group(1)) and m.group(2) not in ('', REDACTED):
            bad.append(m.group(1))
    return bad


class CassetteRecorder:
    def __init__(self, inner, *, redact=(), note=''):
        if not callable(inner):
            raise ValueError('inner must be the http callable to record')
        self._inner = inner
        self._values = set()
        self._secret_bytes = 0                     # UTF-8 bytes of every value in _values (MAX_SECRET_TOTAL_BYTES)
        self._cache = PatternCache()                # compiled patterns, owned here
        self._last_response = None
        self._unproducible = None              # set: to_json fails closed
        self.interactions = []
        self.checkpoints = []                  # interaction counts at scenario checkpoints
        self.note = str(note)
        for v in redact:
            check_value(v)                         # refuse, never drop, a value that cannot be redacted safely
            if not self._register(v):
                raise ValueError(f'the redaction values exceed {MAX_SECRET_TOTAL_BYTES} UTF-8 bytes together')

    def __repr__(self):
        return f'CassetteRecorder(interactions={len(self.interactions)}, redactions=<{len(self._values)}>)'

    def _register(self, value):
        """The ONLY way a value enters _values, so the byte caps hold before any pattern is compiled or any form is
        derived from it (Codex P2 on bcc2d8d). A refused value is never added: the cassette becomes unproducible.
        Raises ValueError for a single value above MAX_SECRET_BYTES; returns False when the total would pass."""
        if value in self._values:
            return True
        n = secret_cost(value)
        if self._secret_bytes + n > MAX_SECRET_TOTAL_BYTES:
            self._unproducible = 'too much secret text to redact'
            return False
        self._secret_bytes += n
        self._values.add(value)
        return True

    def _learn(self, value):
        if isinstance(value, str) and len(value) >= 8 and value != REDACTED and value not in self._values:
            if len(self._values) >= MAX_LEARNED_VALUES:      # NEW-P1: N values x every text is quadratic
                self._unproducible = 'too many distinct secret values to redact'
                return                                       # the name-based blank already happened; fail closed
            try:
                self._register(value)
            except ValueError:
                self._unproducible = 'a secret value too long to redact'

    # ---- sanitizing ----

    def _clean_text(self, text):
        def json_sub(m):
            if is_sensitive_name(_json_text(m.group(1))) and m.group(3) not in ('null', f'"{REDACTED}"'):
                v = m.group(3)
                self._learn(_json_text(v[1:-1]) if v.startswith('"') else v)
                return f'"{m.group(1)}"{m.group(2)}"{REDACTED}"'
            return m.group(0)

        def form_sub(m):
            if is_sensitive_name(m.group(1)) and m.group(2) not in ('', REDACTED):
                self._learn(urllib.parse.unquote_plus(m.group(2)))
                self._learn(m.group(2))
                return f'{m.group(1)}={REDACTED}'
            return m.group(0)
        text = _JSON_PAIR.sub(json_sub, str(text))
        text = _FORM_PAIR.sub(form_sub, text)
        if self._unproducible:                       # failing closed anyway: no N x size value scrub (NEW-P1)
            return text
        return redact_values(text, self._values, self._cache)

    def _clean_pairs(self, pairs, allowed=None):
        """allowed(name): the allow-list; a value under any other name is stored as <redacted> (fail closed)."""
        out = []
        for k, v in pairs:
            if is_sensitive_name(k):
                self._learn(v)
                out.append([k, REDACTED])
            elif allowed is not None and not allowed(k) and v != '':
                out.append([k, REDACTED])
            elif allowed is A.header_allowed and TOKEN_RUN.search(str(v)):  # NEW-R3: a key-like header value
                out.append([k, REDACTED])
            else:
                out.append([k, v])
        return [[k, redact_values(v, self._values, self._cache) if v != REDACTED else v] for k, v in out]

    def _request_record(self, request):
        key = request.wire_header(API_KEY_HEADER)
        if key is not None:
            try:
                check_value(key)
            except ValueError:
                raise CassetteLeak('the API key on the wire is too short to be redacted safely; not recorded') \
                    from None
            try:
                ok = self._register(key)
            except ValueError:
                ok = False
            if not ok:
                raise CassetteLeak('the API key on the wire cannot be redacted within the caps; not recorded')
        pairs = urllib.parse.parse_qsl(request.query, keep_blank_values=True)
        query = self._clean_pairs(pairs, A.param_allowed)
        headers = self._clean_pairs(list(request.headers), A.header_allowed)
        return {'method': request.method, 'url': redact_values(request.url, self._values, self._cache),
                'signed': bool(request.signed), 'query': query, 'headers': headers}

    def _occurs(self, values, interactions):
        """Does any newly learned value occur in the already stored interactions? Matched on the RAW strings (V1:
        a JSON dump escapes quotes, backslashes and control characters, so a value containing one would be missed)
        and in binary bodies. Only then is the full rescrub needed (a per-request signature never is)."""
        if any(contains_values(s, values, self._cache) for s in _strings(interactions)):
            return True
        for it in interactions:
            b64 = it.get('response', {}).get('body_b64')
            if b64 and contains_values(base64.b64decode(b64).decode('latin-1'), values, self._cache):
                return True
        return False

    def _rescrub(self, obj, key=None):
        """Re-apply value redaction to already stored interactions (a value learned later, e.g. a listenKey first
        seen in a later answer, must not survive in an earlier one)."""
        if isinstance(obj, dict):
            return {k: self._rescrub(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._rescrub(v) for v in obj]
        if isinstance(obj, str):
            if key == 'body_b64':
                b = base64.b64decode(obj).decode('latin-1')
                return base64.b64encode(redact_values(b, self._values, self._cache).encode('latin-1')).decode('ascii')
            return redact_values(obj, self._values, self._cache)
        return obj

    def __call__(self, request):
        known = set(self._values)
        try:
            self._record(request)
        finally:
            new = self._values - known
            if new and len(self.interactions) > 1 and self._occurs(new, self.interactions[:-1]):
                self.interactions = self._rescrub(self.interactions)
        return self._last_response

    def _record(self, request):
        self._last_response = None
        req = self._request_record(request)
        try:
            resp = self._inner(request)
        except Exception as ex:
            name = _error_name(ex)
            if name is not None:
                rec = {'request': req, 'error': name}
                if isinstance(ex, WireNotSent):          # a token, never free text (it could carry anything)
                    reason = str(ex.reason)
                    rec['reason'] = reason if _REASON_RE.fullmatch(reason) else 'unspecified'
                self.interactions.append(rec)
            raise
        headers = dict(self._clean_pairs([(str(k), str(v)) for k, v in dict(resp.headers).items()],
                                         A.header_allowed))
        raw = bytes(resp.body)
        if len(raw) > MAX_RECORD_BODY:
            self._unproducible = 'an answer larger than the recordable maximum'
            payload = {'body_omitted': 'too_large'}
            self.interactions.append({'request': req, 'response': dict(status=resp.status, headers=headers,
                                                                     **payload)})
            self._last_response = resp
            return
        try:
            text = raw.decode('utf-8')
            try:
                allowed = A.sanitize_json(text, self._learn)
            except A.TooDeep:
                self._unproducible = 'an answer nested deeper than the recordable maximum'
                allowed, text = None, ''
                payload = {'body_omitted': 'too_deep'}
            else:
                if allowed is None:
                    payload = {'body_text': self._clean_text(text)}
                elif self._unproducible:               # failing closed anyway: skip the N x size scrub (NEW-P1)
                    payload = {'body_text': allowed}
                else:
                    payload = {'body_text': redact_values(allowed, self._values, self._cache)}
        except UnicodeDecodeError:
            cleaned = self._clean_text(raw.decode('latin-1')).encode('latin-1')
            payload = {'body_b64': base64.b64encode(cleaned).decode('ascii')}
        self.interactions.append({'request': req, 'response': dict(status=resp.status, headers=headers, **payload)})
        self._last_response = resp

    # ---- output ----

    def _audit(self, text, doc, budget=None, first=0):
        """budget=None: the document is charged here first. text=None: the serialized form is built here, after the
        charge (to_json charges, serializes once, then audits with the same budget). The per-interaction checks walk
        doc['interactions'] (a segment's own); first = the global index of its first interaction (messages)."""
        values = sorted(self._values)
        if self._unproducible:
            raise CassetteLeak(f'{self._unproducible}; cassette not produced')
        # Codex P2s on bc5a351 / bcc2d8d: the completed document is charged to the budget as a STRUCTURE, in bytes,
        # BEFORE it is serialized and before any regex, value scan or generated form; with or without registered
        # values (leak_audit of a replay document has none). The secret caps hold at registration (_register).
        if budget is None:
            budget = _AuditBudget()
            budget.document(doc)
        if sum(_utf8_len(v) for v in values) > MAX_SECRET_TOTAL_BYTES:      # defence in depth
            raise CassetteLeak('too much secret text to audit; cassette not produced')
        if text is None:
            text = json.dumps(doc, ensure_ascii=False)
        if contains_values(text, values, self._cache) or \
                any(contains_values(s, values, self._cache) for s in _strings_and_keys(doc)):
            raise CassetteLeak('a secret value is still present; cassette not produced')
        # First testnet 5a run (38da6f7): one account-wide positionRisk / account body holds ~4,500 base64-like runs,
        # so a multi-cycle scenario cassette passed MAX_DECODED_TOKENS as ONE text and every cassette was refused.
        # The encoded-form audit runs per string leaf and per object key instead (each bounded on its own, and all of
        # them together by one _AuditBudget). Codex P1s on 1297ee3: it walks the COMPLETED document (the recorder note
        # under _provenance included), and every string is also searched for the base64 forms generated from each
        # secret (exact spelling, any byte alignment). Codex P1 on bc5a351: runs are decoded down to _B64_MIN, so a
        # case-variant of a short secret is matched case-insensitively on the decoded text.
        if values:
            # ONE pattern per audit: the generated base64 forms (exact case) and every value's own case-insensitive
            # pattern (the PatternCache ones; contains_values' percent-decoded forms are views already).
            forms = b64_forms(values)
            found = re.compile('|'.join([re.escape(f) for f in sorted(forms, key=len, reverse=True)]
                                        + ['(?i:%s)' % self._cache.get(v).pattern for v in values])).search
            # Codex P1 on bcc2d8d: decoded_views decodes runs on EVERY surface of the string (the percent-decoded one
            # included), so a percent-encoded base64 of a case-variant is decoded and matched case-insensitively.
            for s in _strings_and_keys(doc):
                for view in decoded_views(s, budget):
                    if found(view):
                        raise CassetteLeak('a secret value is present in an encoded form; cassette not produced')
        for n, it in enumerate(doc['interactions'], first):     # the audited document's own (a segment's)
            req = it['request']
            resp = it.get('response', {})
            pairs = list(req['query']) + list(req['headers']) + list(resp.get('headers', {}).items())
            if any(is_sensitive_name(k) and v not in _CLEARED for k, v in pairs):
                raise CassetteLeak(f'interaction {n}: a sensitive parameter or header still has a value')
            bodies = []
            if 'body_text' in resp:
                bodies.append(resp['body_text'])
                try:
                    parsed = json.loads(resp['body_text'])
                except ValueError:
                    parsed = None
                if _sensitive_json_leftovers(parsed):
                    raise CassetteLeak(f'interaction {n}: a sensitive JSON field still has a value')
            if 'body_b64' in resp:
                b = base64.b64decode(resp['body_b64']).decode('latin-1')
                if contains_values(b, values, self._cache):
                    raise CassetteLeak(f'interaction {n}: a secret value is still present in a binary body')
                bodies.append(b)
            for b in bodies:
                if _sensitive_text_leftovers(b):
                    raise CassetteLeak(f'interaction {n}: a sensitive field in the body still has a value')

    def _provenance(self, extra=''):
        return ('Recorded through newcore.venue.cassette.CassetteRecorder. Sensitive names (signature, API key, '
                'listenKey, secret, token, cookie, ...) and registered secret values are replaced by <redacted>. '
                + self.note + extra).strip()

    def to_json(self):
        doc = {'format': CASSETTE_FORMAT, '_provenance': self._provenance(), 'interactions': self.interactions}
        return self._audited_text(doc)

    def _audited_text(self, doc, first=0):
        budget = _AuditBudget()
        budget.document(doc)                         # charged before the one serialization (Codex P2 on bcc2d8d)
        text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)
        try:                                         # NEW-S1: a lone surrogate (\ud800) cannot be written as UTF-8
            text.encode('utf-8')
        except UnicodeEncodeError:
            raise CassetteLeak('an answer holds text that is not encodable (lone surrogate); cassette not '
                               'produced') from None
        self._audit(text, doc, budget, first)        # a pure check: anything left raises, nothing is repaired here
        return text

    # ---- segmented evidence (Codex T04 triage 6087020052) ----

    def checkpoint(self):
        """Evidence only: a scenario checkpoint (a Runner cycle boundary) after the interactions so far, where
        to_segments() may close a segment. Touches nothing but this list; never raises into the caller."""
        n = len(self.interactions)
        if n and (not self.checkpoints or self.checkpoints[-1] < n):
            self.checkpoints.append(n)

    def _segment_ranges(self):
        """[(first, end), ...]: consecutive checkpoint ranges packed in order while a segment holds less than
        SEGMENT_CHARS (charged exactly as the audit charges a document). The WHOLE cassette above MAX_AUDIT_CHARS, or
        more than MAX_SEGMENTS segments, is a refusal."""
        n = len(self.interactions)
        cuts = [c for c in sorted(set(self.checkpoints)) if 0 < c < n] + [n]
        total, out, start, size, prev = 0, [], 0, 0, 0
        for c in cuts:
            b = _AuditBudget()
            b.document(self.interactions[prev:c])            # one range alone above any cap: refused here
            total += b.chars
            if total > MAX_AUDIT_CHARS:
                raise CassetteLeak('too much text to audit; cassette not produced')
            if size and size + b.chars > SEGMENT_CHARS:
                out.append((start, prev))
                start, size = prev, 0
            size += b.chars
            prev = c
        out.append((start, n))
        if len(out) > MAX_SEGMENTS:
            raise CassetteLeak('too many cassette segments to audit; cassette not produced')
        return out

    def to_segments(self):
        """[(text, first interaction index, interaction count), ...]: the cassette as complete zb-newcore-cassette/1
        documents split at checkpoints (exactly to_json()'s one document when one segment holds everything). EVERY
        segment is charged, serialized once and leak-audited with its OWN budget against every secret value, all of
        them before anything is returned: any refusal raises CassetteLeak and nothing is produced. Each segment is
        audited once (no whole-document audit first: no interaction is decoded twice)."""
        if self._unproducible:
            raise CassetteLeak(f'{self._unproducible}; cassette not produced')
        ranges = self._segment_ranges()
        if len(ranges) == 1:
            return [(self.to_json(), 0, len(self.interactions))]
        out = []
        for i, (first, end) in enumerate(ranges, 1):
            doc = {'format': CASSETTE_FORMAT, '_provenance': self._provenance(f' Segment {i} of {len(ranges)}.'),
                   'segment': {'index': i, 'count': len(ranges), 'first': first},
                   'interactions': self.interactions[first:end]}
            out.append((self._audited_text(doc, first), first, end - first))
        return out

    def save(self, path):
        text = self.to_json()                        # raises CassetteLeak before anything touches the disk
        tmp = f'{path}.tmp{os.getpid()}'
        try:
            with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:                                     # never a leftover *.tmpPID file
            if os.path.exists(tmp):
                os.remove(tmp)
        return path


def _split_pairs(pairs, hidden=frozenset()):
    """(stable value-compared pairs, sorted names of sensitive / hidden pairs compared by presence)."""
    stable = [(k, v) for k, v in pairs if k not in VOLATILE_PARAMS and not is_sensitive_name(k) and k not in hidden]
    sensitive = sorted(k for k, _ in pairs if is_sensitive_name(k) or k in hidden)
    return stable, sensitive


class CassettePlayer:
    def __init__(self, source):
        if isinstance(source, (str, os.PathLike)):
            with open(source, encoding='utf-8') as fh:
                doc = json.load(fh)
        else:
            doc = source
        if not isinstance(doc, dict) or doc.get('format') != CASSETTE_FORMAT or \
                not isinstance(doc.get('interactions'), list):
            raise CassetteMismatch('not a ' + CASSETTE_FORMAT + ' cassette')
        self._items = doc['interactions']
        self._next = 0

    def __repr__(self):
        return f'CassettePlayer(played={self._next}, total={len(self._items)})'

    @property
    def remaining(self):
        return len(self._items) - self._next

    def assert_exhausted(self):
        if self.remaining:
            raise CassetteMismatch(f'{self.remaining} recorded interaction(s) were not replayed')

    def __call__(self, request):
        i = self._next
        if i >= len(self._items):
            raise CassetteMismatch(f'cassette exhausted at request {i} ({request.method} {request.url})')
        rec = self._items[i]
        want = rec['request']
        if (request.method, request.url, bool(request.signed)) != (want['method'], want['url'], want['signed']):
            raise CassetteMismatch(f'request {i}: expected {want["method"]} {want["url"]}, '
                                   f'got {request.method} {request.url}')
        hidden = {k for k, v in want['query'] if v == REDACTED}      # allow-list redactions: presence only
        got_pairs, got_sens = _split_pairs(urllib.parse.parse_qsl(request.query, keep_blank_values=True), hidden)
        want_pairs, want_sens = _split_pairs([tuple(p) for p in want['query']], hidden)
        if got_pairs != want_pairs or got_sens != want_sens:
            names = sorted(({k for k, _ in got_pairs} ^ {k for k, _ in want_pairs}) | (set(got_sens) ^ set(want_sens))) \
                or sorted({k for (k, v), (k2, v2) in zip(got_pairs, want_pairs) if (k, v) != (k2, v2)})
            raise CassetteMismatch(f'request {i}: query differs (params: {", ".join(names) or "order"})')
        if request.signed:
            has_sig = any(k == 'signature' and v for k, v in urllib.parse.parse_qsl(request.query))
            if not has_sig or not request.wire_header(API_KEY_HEADER):
                raise CassetteMismatch(f'request {i}: a signed request must carry a key header and a signature')
        self._next += 1
        resp = rec.get('response')                   # finding 4: exactly one outcome, exactly one body form
        if ('error' in rec) == (resp is not None) or (isinstance(resp, dict) and
                                                     sum(k in resp for k in ('body_text', 'body_b64')) != 1):
            raise CassetteMismatch(f'request {i}: malformed recorded interaction')
        if 'error' in rec:
            if rec['error'] not in _ERRORS:
                raise CassetteMismatch(f'request {i}: unknown recorded error')
            if rec['error'] == 'WireNotSent':
                raise WireNotSent('replayed WireNotSent', str(rec.get('reason', 'unspecified'))[:20])
            raise _ERRORS[rec['error']]('replayed ' + rec['error'])
        try:                                       # a malformed record is a mismatch, never a silent answer
            r = rec['response']
            if 'body_text' in r:
                body = r['body_text'].encode('utf-8')
            else:
                body = base64.b64decode(r['body_b64'], validate=True)
            status = int(r['status'])
            if not 100 <= status <= 599:
                raise ValueError('status')
            return HttpResponse(status, dict(r['headers']), body)
        except (KeyError, TypeError, ValueError):
            raise CassetteMismatch(f'request {i}: malformed recorded answer') from None
