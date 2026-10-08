"""Wire types for the injected HTTP function, plus rate-limit header parsing.

The transport never opens a socket. It calls an injected `http(request: HttpRequest) -> HttpResponse`. That function
must either return an HttpResponse or raise WireTimeout / WireConnectionError. ANY other exception is also mapped to
UNKNOWN by the transport (never to "failed"), and its message is never kept (it may hold the signed URL).
"""
import re
from dataclasses import dataclass

_REDACTED = '<redacted>'


class WireTimeout(Exception):
    """The request may have reached Binance; no answer arrived in time."""


class WireConnectionError(Exception):
    """Connection refused / reset / dropped. If it happened after the request was written, it may have been executed."""


@dataclass(frozen=True)
class HttpRequest:
    """One request. url has no query string; query is the full encoded query (signature included for SIGNED calls).
    repr/str never show the API-key header value or the signature."""
    method: str
    url: str
    query: str
    headers: tuple          # ((name, value), ...)
    timeout_s: float
    signed: bool

    def full_url(self):
        return self.url + ('?' + self.query if self.query else '')

    def header(self, name):
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    def redacted_query(self):
        return re.sub(r'(^|&)signature=[^&]*', r'\1signature=' + _REDACTED, self.query)

    def __repr__(self):
        hdrs = tuple((k, _REDACTED if k.lower() == 'x-mbx-apikey' else v) for k, v in self.headers)
        return (f'HttpRequest(method={self.method!r}, url={self.url!r}, query={self.redacted_query()!r}, '
                f'headers={hdrs!r}, timeout_s={self.timeout_s!r}, signed={self.signed!r})')

    __str__ = __repr__


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict           # header name -> value (any case; lookups are case-insensitive)
    body: bytes

    def __repr__(self):
        return f'HttpResponse(status={self.status!r}, headers={self.headers!r}, body=<{len(self.body)} bytes>)'


@dataclass(frozen=True)
class RateLimitUsage:
    """What Binance reported about our usage on this answer. Surfaced only; scheduling is deferred (NC-04).
    used_weight / order_count: ((interval, count), ...) sorted by interval, e.g. (('1m', 12),).
    retry_after_s: the Retry-After header in whole seconds, when present and numeric."""
    used_weight: tuple = ()
    order_count: tuple = ()
    retry_after_s: object = None

    def weight(self, interval='1m'):
        return dict(self.used_weight).get(interval.lower())

    def orders(self, interval):
        return dict(self.order_count).get(interval.lower())


_WEIGHT = re.compile(r'^x-mbx-used-weight-([0-9]+[smhd])$')
_ORDERS = re.compile(r'^x-mbx-order-count-([0-9]+[smhd])$')
_INT = re.compile(r'^[0-9]{1,12}$')


def parse_rate_limits(headers):
    """Parse X-MBX-USED-WEIGHT-<n><unit>, X-MBX-ORDER-COUNT-<n><unit> and Retry-After. Never raises; malformed values
    are skipped (a header is evidence about usage, not a reason to fail the call)."""
    weight, orders, retry = {}, {}, None
    try:
        items = list((headers or {}).items())
    except Exception:
        items = []
    for name, value in items:
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        n, v = name.strip().lower(), value.strip()
        if not _INT.match(v):
            continue
        m = _WEIGHT.match(n)
        if m:
            weight[m.group(1)] = int(v)
            continue
        m = _ORDERS.match(n)
        if m:
            orders[m.group(1)] = int(v)
            continue
        if n == 'retry-after':
            retry = int(v)
    return RateLimitUsage(tuple(sorted(weight.items())), tuple(sorted(orders.items())), retry)
