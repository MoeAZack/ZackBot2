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


class WireResponseTooLarge(WireConnectionError):
    """The answer exceeded the sender's size bound and was not read in full: its content is unknown."""


class WireSeamError(Exception):
    """A setup/programming fault in the injected HTTP seam itself (e.g. a cassette that does not match the request).
    The transport re-raises it instead of mapping it to UNKNOWN, so a broken harness can never look like venue data."""


API_KEY_HEADER = 'X-MBX-APIKEY'


class HttpRequest:
    """One request. url has no query string; query is the full encoded query (signature included for SIGNED calls).

    The API key is never stored in a readable field (Cowork finding 5): `headers` is a REDACTED view (the key header
    reads '<redacted>'), and the raw key is produced only by wire_headers(), which the sender calls at the moment it
    writes the request. The raw value comes from `key_provider` (the transport passes its credential source's
    api_key, so the key is fetched at send time). A raw key given in `headers` is moved behind a provider at
    construction. The class is slotted and immutable: dataclasses.asdict() and vars() do not apply to it.
    repr/str show neither the key nor the signature."""
    __slots__ = ('method', 'url', 'query', 'headers', 'timeout_s', 'signed', '_key_provider')

    def __init__(self, method, url, query, headers, timeout_s, signed, key_provider=None):
        view, raw = [], None
        for k, v in tuple(headers):
            if isinstance(k, str) and k.lower() == API_KEY_HEADER.lower():
                if v != _REDACTED:
                    raw = v
                view.append((k, _REDACTED))
            else:
                view.append((k, v))
        if key_provider is None and raw is not None:
            def key_provider(_raw=raw):
                return _raw
        for name, value in (('method', method), ('url', url), ('query', query), ('headers', tuple(view)),
                            ('timeout_s', timeout_s), ('signed', signed), ('_key_provider', key_provider)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name, value):
        raise AttributeError('HttpRequest is immutable')

    __delattr__ = __setattr__

    def __reduce_ex__(self, protocol):
        raise TypeError('HttpRequest cannot be pickled or copied')

    def __eq__(self, other):
        if not isinstance(other, HttpRequest):
            return NotImplemented
        return all(getattr(self, n) == getattr(other, n) for n in ('method', 'url', 'query', 'headers',
                                                                   'timeout_s', 'signed'))

    __hash__ = None

    def full_url(self):
        return self.url + ('?' + self.query if self.query else '')

    def header(self, name):
        """From the REDACTED view: the API-key header reads '<redacted>'."""
        for k, v in self.headers:
            if k.lower() == name.lower():
                return v
        return None

    def wire_headers(self):
        """The headers as they go on the wire, with the raw API key. Only the sender (or a seam recording what is
        sent) calls this; never log the result."""
        out = []
        for k, v in self.headers:
            if k.lower() == API_KEY_HEADER.lower():
                if self._key_provider is None:
                    raise WireSeamError('the request has an API-key header but no key provider')
                v = self._key_provider()
            out.append((k, v))
        return tuple(out)

    def wire_header(self, name):
        for k, v in self.wire_headers():
            if k.lower() == name.lower():
                return v
        return None

    def redacted_query(self):
        return re.sub(r'(^|&)signature=[^&]*', r'\1signature=' + _REDACTED, self.query)

    def __repr__(self):
        return (f'HttpRequest(method={self.method!r}, url={self.url!r}, query={self.redacted_query()!r}, '
                f'headers={self.headers!r}, timeout_s={self.timeout_s!r}, signed={self.signed!r})')

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
        if not _INT.fullmatch(v):
            continue
        m = _WEIGHT.fullmatch(n)
        if m:
            weight[m.group(1)] = int(v)
            continue
        m = _ORDERS.fullmatch(n)
        if m:
            orders[m.group(1)] = int(v)
            continue
        if n == 'retry-after':
            retry = int(v)
    return RateLimitUsage(tuple(sorted(weight.items())), tuple(sorted(orders.items())), retry)
