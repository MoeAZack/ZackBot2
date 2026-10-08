"""Cassette seam for S5: record sanitized request/response pairs from a live run, replay them as the injected `http`.

Recording (owner-run S5 smoke):  http = CassetteRecorder(TestnetHttpSender(), redact=(...))
Replay (CI, no network):         http = CassettePlayer('cassette.json')

Sanitizing (structural, then literal):
- the X-MBX-APIKEY header value and the `signature` query value are replaced by <redacted>;
- every literal value the recorder has seen as an API key or signature, plus any `redact=` values (e.g. what the
  SecretScrubber holds), is replaced in request, response headers and body (text or bytes) before it is stored;
- to_json() re-checks the serialized cassette for every one of those values and raises CassetteLeak instead of
  producing a file that still holds one. The secret itself never travels on the wire (only its HMAC), so it can only
  appear if a seam bug put it there, and the check would catch that when it is registered via redact=.

Replay matches interactions in order on method, URL, the signed flag and every query parameter except the volatile
`timestamp` and `signature` (a replay signs with its own clock and dummy key). A signed replay request must still
carry an API-key header and a signature. A mismatch or an exhausted cassette raises CassetteMismatch, a WireSeamError
the transport re-raises (a broken harness must never look like venue data).
"""
import base64
import json
import os
import urllib.parse

from .wire import HttpResponse, WireConnectionError, WireResponseTooLarge, WireSeamError, WireTimeout

CASSETTE_FORMAT = 'zb-newcore-cassette/1'
VOLATILE_PARAMS = ('timestamp', 'signature')
_REDACTED = '<redacted>'
_ERRORS = {'WireTimeout': WireTimeout, 'WireResponseTooLarge': WireResponseTooLarge,
           'WireConnectionError': WireConnectionError}


class CassetteMismatch(WireSeamError):
    """The replayed request does not match the next recorded interaction (or the cassette is exhausted)."""


class CassetteLeak(WireSeamError):
    """A registered secret value is still present in the serialized cassette; nothing was written."""


def _error_name(ex):
    for name in ('WireTimeout', 'WireResponseTooLarge', 'WireConnectionError'):
        if isinstance(ex, _ERRORS[name]):
            return name
    return None


class CassetteRecorder:
    def __init__(self, inner, *, redact=(), note=''):
        if not callable(inner):
            raise ValueError('inner must be the http callable to record')
        self._inner = inner
        self._redact = set()
        self.interactions = []
        self.note = str(note)
        self.add_redactions(*redact)

    def __repr__(self):
        return f'CassetteRecorder(interactions={len(self.interactions)}, redactions=<{len(self._redact)}>)'

    def add_redactions(self, *values):
        for v in values:
            if isinstance(v, str) and len(v) >= 8:
                self._redact.add(v)

    def _scrub(self, text):
        for v in sorted(self._redact, key=len, reverse=True):
            text = text.replace(v, _REDACTED)
        return text

    def _scrub_bytes(self, data):
        for v in sorted(self._redact, key=len, reverse=True):
            data = data.replace(v.encode('utf-8'), _REDACTED.encode())
        return data

    def _request_record(self, request):
        pairs = urllib.parse.parse_qsl(request.query, keep_blank_values=True)
        for k, v in pairs:
            if k == 'signature':
                self.add_redactions(v)
        key = request.wire_header('X-MBX-APIKEY')
        if key:
            self.add_redactions(key)
        return {
            'method': request.method, 'url': request.url, 'signed': bool(request.signed),
            'query': [[k, _REDACTED if k == 'signature' else self._scrub(v)] for k, v in pairs],
            'headers': [[k, _REDACTED if k.lower() == 'x-mbx-apikey' else self._scrub(v)] for k, v in request.headers],
        }

    def __call__(self, request):
        req = self._request_record(request)
        try:
            resp = self._inner(request)
        except Exception as ex:
            name = _error_name(ex)
            if name is not None:
                self.interactions.append({'request': req, 'error': name})
            raise
        body = self._scrub_bytes(bytes(resp.body))
        try:
            payload = {'body_text': body.decode('utf-8')}
        except UnicodeDecodeError:
            payload = {'body_b64': base64.b64encode(body).decode('ascii')}
        self.interactions.append({'request': req, 'response': dict(
            status=resp.status, headers={str(k): self._scrub(str(v)) for k, v in dict(resp.headers).items()},
            **payload)})
        return resp

    def to_json(self):
        doc = {'format': CASSETTE_FORMAT,
               '_provenance': ('Recorded through newcore.venue.cassette.CassetteRecorder. API-key header, signature '
                               'and registered secret values are replaced by <redacted>. ' + self.note).strip(),
               'interactions': self.interactions}
        text = json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False)
        for v in self._redact:
            if v in text or json.dumps(v)[1:-1] in text:
                raise CassetteLeak('a registered secret value is still present; cassette not produced')
            for i in self.interactions:
                b64 = i.get('response', {}).get('body_b64')
                if b64 and v.encode('utf-8') in base64.b64decode(b64):
                    raise CassetteLeak('a registered secret value is still present; cassette not produced')
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


def _stable_pairs(query):
    return [(k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True) if k not in VOLATILE_PARAMS]


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
        got_pairs = _stable_pairs(request.query)
        want_pairs = [(k, v) for k, v in want['query'] if k not in VOLATILE_PARAMS]
        if got_pairs != want_pairs:
            names = sorted({k for k, _ in got_pairs} ^ {k for k, _ in want_pairs}) or \
                sorted({k for (k, v), (k2, v2) in zip(got_pairs, want_pairs) if (k, v) != (k2, v2)})
            raise CassetteMismatch(f'request {i}: query differs (params: {", ".join(names) or "order"})')
        if request.signed:
            has_sig = any(k == 'signature' and v for k, v in urllib.parse.parse_qsl(request.query))
            if not has_sig or not request.wire_header('X-MBX-APIKEY'):
                raise CassetteMismatch(f'request {i}: a signed request must carry a key header and a signature')
        self._next += 1
        if 'error' in rec:
            raise _ERRORS[rec['error']]('replayed ' + rec['error'])
        r = rec['response']
        body = r['body_text'].encode('utf-8') if 'body_text' in r else base64.b64decode(r['body_b64'])
        return HttpResponse(int(r['status']), dict(r['headers']), body)
