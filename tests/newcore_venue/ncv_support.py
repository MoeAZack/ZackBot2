"""Shared helpers for the NC-03 wire-layer tests: fixture loading, a recording fake HTTP function, dummy credentials.
No network: FakeHttp never opens a socket; it records each HttpRequest and replays a scripted answer."""
import json
import os
from decimal import Decimal

from newcore.venue.credentials import StaticCredentials
from newcore.venue.transport import BinanceTestnetTransport, PositionMode
from newcore.venue.wire import HttpResponse

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, 'fixtures', 'binance_usdm_shapes.json')

# DUMMY credentials (never real). Distinctive so a leak is easy to grep for.
DUMMY_KEY = 'DUMMYKEYzzLEAKCHECKqq0123456789abcdefABCDEF0123456789abcdefABCD'
DUMMY_SECRET = 'DUMMYSECRETzzLEAKCHECKqq9876543210fedcbaFEDCBA9876543210fedcba'
NOW_MS = 1759917600000


def fixture(name):
    with open(FIXTURES, encoding='utf-8') as fh:
        data = json.load(fh)
    f = data[name]
    body = f['body_text'].encode('utf-8') if 'body_text' in f else json.dumps(f['body']).encode('utf-8')
    return HttpResponse(f['status'], dict(f['headers']), body)


def raw(status, body_bytes, headers=None):
    return HttpResponse(status, dict(headers or {}), body_bytes)


class FakeHttp:
    """Scripted answers: each item is an HttpResponse, an exception instance (raised), or a fixture name."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return fixture(item)
        return item

    @property
    def last(self):
        return self.requests[-1]


def make(*script, mode=PositionMode.HEDGE, creds=True, clock=None):
    http = FakeHttp(*script)
    t = BinanceTestnetTransport(environment='testnet', http=http, clock=clock or (lambda: NOW_MS),
                                position_mode=mode,
                                credentials=StaticCredentials(DUMMY_KEY, DUMMY_SECRET) if creds else None)
    return t, http


def query_pairs(request):
    """The request's query as an ordered list of (name, value), URL-decoded."""
    from urllib.parse import parse_qsl
    return parse_qsl(request.query, keep_blank_values=True, strict_parsing=True)


D = Decimal
