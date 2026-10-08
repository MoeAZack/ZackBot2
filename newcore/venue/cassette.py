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
  again for every value, and every query pair, header, JSON field (parsed, any depth) and form pair is checked for a
  sensitive name that still carries a value. Anything left raises CassetteLeak and nothing is produced.

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

from . import cassette_allow as A

from .redact import REDACTED, PatternCache, check_value, contains_values, is_sensitive_name, redact_values
from .wire import (API_KEY_HEADER, HttpResponse, WireConnectionError, WireNotSent, WireResponseTooLarge, WireSeamError,
                   WireTimeout)

CASSETTE_FORMAT = 'zb-newcore-cassette/1'
VOLATILE_PARAMS = ('timestamp',)
_ERRORS = {'WireTimeout': WireTimeout, 'WireNotSent': WireNotSent, 'WireResponseTooLarge': WireResponseTooLarge,
           'WireConnectionError': WireConnectionError}
_CLEARED = (REDACTED, '', None)
MAX_RECORD_BODY = 8 * 1024 * 1024           # a larger answer is not stored (the cassette becomes unproducible)
_REASON_RE = re.compile(r'[a-z_]{1,20}')
_B64_RUN = re.compile(r'[A-Za-z0-9+/_-]{16,}={0,2}')
_HEX_RUN = re.compile(r'(?:[0-9a-fA-F]{2}){8,}')
MAX_DECODED_TOKENS = 20000


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


def decoded_views(text):
    """Text forms a registered secret could hide in (Cowork #37 R2): NFKC, unicode-escape decoded, and every
    base64 / hex run decoded. Bounded: at most MAX_DECODED_TOKENS runs per text."""
    yield unicodedata.normalize('NFKC', text)
    cur = text
    for _ in range(3):                         # nested escaping (a JSON string inside a JSON document inside ...)
        try:
            cur = cur.encode('latin-1', 'backslashreplace').decode('unicode_escape')
        except (UnicodeDecodeError, ValueError):
            break
        yield cur
        yield unicodedata.normalize('NFKC', cur)
    n = 0
    for m in _B64_RUN.finditer(text):
        n += 1
        if n > MAX_DECODED_TOKENS:
            break
        tok = m.group(0)
        for fn in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                yield fn(tok + '=' * (-len(tok) % 4)).decode('latin-1')
            except (binascii.Error, ValueError):
                pass
    for m in _HEX_RUN.finditer(text):
        n += 1
        if n > 2 * MAX_DECODED_TOKENS:
            break
        try:
            yield bytes.fromhex(m.group(0)).decode('latin-1')
        except ValueError:
            pass

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
        self._cache = PatternCache()                # compiled patterns, owned here
        self._last_response = None
        self._unproducible = None              # set: to_json fails closed
        self.interactions = []
        self.note = str(note)
        for v in redact:
            check_value(v)                         # refuse, never drop, a value that cannot be redacted safely
        self._values.update(redact)

    def __repr__(self):
        return f'CassetteRecorder(interactions={len(self.interactions)}, redactions=<{len(self._values)}>)'

    def _learn(self, value):
        if isinstance(value, str) and len(value) >= 8 and value != REDACTED:
            self._values.add(value)

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
            self._values.add(key)
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
                payload = {'body_text': redact_values(allowed, self._values, self._cache) if allowed is not None
                           else self._clean_text(text)}
        except UnicodeDecodeError:
            cleaned = self._clean_text(raw.decode('latin-1')).encode('latin-1')
            payload = {'body_b64': base64.b64encode(cleaned).decode('ascii')}
        self.interactions.append({'request': req, 'response': dict(status=resp.status, headers=headers, **payload)})
        self._last_response = resp

    # ---- output ----

    def _audit(self, text):
        values = sorted(self._values)
        if self._unproducible:
            raise CassetteLeak(f'{self._unproducible}; cassette not produced')
        if contains_values(text, values, self._cache) or \
                any(contains_values(s, values, self._cache) for s in _strings(self.interactions)):
            raise CassetteLeak('a secret value is still present; cassette not produced')
        if values and any(contains_values(view, values, self._cache) for view in decoded_views(text)):
            raise CassetteLeak('a secret value is present in an encoded form; cassette not produced')
        for n, it in enumerate(self.interactions):
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

    def to_json(self):
        doc = {'format': CASSETTE_FORMAT,
               '_provenance': ('Recorded through newcore.venue.cassette.CassetteRecorder. Sensitive names (signature, '
                               'API key, listenKey, secret, token, cookie, ...) and registered secret values are '
                               'replaced by <redacted>. ' + self.note).strip(),
               'interactions': self.interactions}
        text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)
        self._audit(text)                            # a pure check: anything left raises, nothing is repaired here
        return text

    def save(self, path):
        text = self.to_json()                        # raises CassetteLeak before anything touches the disk
        tmp = f'{path}.tmp{os.getpid()}'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
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
