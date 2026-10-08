"""TNET-01 HTTP seam for injected faults (plan 6.1 seams.py, venue part).

FaultHttp wraps the real `http` (TestnetHttpSender, or a recorder around it) and, while armed, applies one fault kind to
the next `times` requests:
  lost_response     the request IS sent (the venue acts on it); the answer is dropped -> WireTimeout
  timeout           nothing is sent; WireTimeout (the transport cannot tell it from a lost answer: UNKNOWN)
  reset             nothing is sent; WireConnectionError
  http_5xx          nothing is sent; a synthetic 503 answer (UNKNOWN)
  dns_timeout       nothing is sent; WireNotSent('dns_timeout') (provably not sent)
  duplicate_resend  the request is sent TWICE; the second answer is returned (a duplicate client id -> UNKNOWN)
Put it UNDER the cassette recorder (recorder(FaultHttp(sender))) so the cassette shows what the transport saw.

RunDeadline / DeadlinePort (Codex P1 on nc-tnet01, applied to the venue harness too): ONE absolute deadline for the
whole run. RunDeadline.bounded(sleep) never sleeps past it (a wait that would end past it raises DeadlineExceeded
BEFORE sleeping); DeadlinePort refuses an OPENING market order past it, before it is sent. A risk-reducing send (a
reduce-only close, a protective stop, a cancel) still goes through: refusing it would only leave exposure for the
teardown, which sends the same kind of order.
"""
from .wire import HttpResponse, WireConnectionError, WireNotSent, WireTimeout

FAULT_KINDS = ('lost_response', 'timeout', 'reset', 'http_5xx', 'dns_timeout', 'duplicate_resend')


class FaultHttp:
    def __init__(self, inner):
        if not callable(inner):
            raise ValueError('inner must be the http callable')
        self._inner, self._kind, self._left = inner, None, 0
        self.injected = []                   # (kind, method, path) actually applied

    def arm(self, kind, times=1):
        if kind not in FAULT_KINDS:
            raise ValueError(f'unknown fault kind {kind!r}')
        if type(times) is not int or times < 1:
            raise ValueError('times must be a positive int')
        self._kind, self._left = kind, times

    def disarm(self):
        self._kind, self._left = None, 0

    @property
    def armed(self):
        return self._left > 0

    def __call__(self, request):
        if self._left <= 0:
            return self._inner(request)
        kind = self._kind
        self._left -= 1
        if self._left == 0:
            self._kind = None
        self.injected.append((kind, request.method, request.url.split('binancefuture.com', 1)[-1]))
        if kind == 'lost_response':
            self._inner(request)
            raise WireTimeout('injected: answer lost after send')
        if kind == 'timeout':
            raise WireTimeout('injected timeout')
        if kind == 'reset':
            raise WireConnectionError('injected reset')
        if kind == 'http_5xx':
            return HttpResponse(503, {}, b'{"code": -1001, "msg": "injected 503"}')
        if kind == 'dns_timeout':
            raise WireNotSent('injected', 'dns_timeout')
        self._inner(request)                                  # duplicate_resend: the first send is acted on
        return self._inner(request)


class DeadlineExceeded(Exception):
    """The run's absolute deadline has passed (or a wait would end past it): nothing may open exposure any more."""


class RunDeadline:
    def __init__(self, deadline_s, monotonic):
        if isinstance(deadline_s, bool) or not isinstance(deadline_s, (int, float)) or not 0 < deadline_s <= 7200:
            raise ValueError('deadline_s must be a number in (0, 7200]')
        self._mono, self.deadline_s = monotonic, deadline_s
        self.at = monotonic() + deadline_s

    def remaining(self):
        return self.at - self._mono()

    def expired(self):
        return self._mono() > self.at

    def bounded(self, sleep):
        def bounded_sleep(seconds):
            left = self.remaining()
            if seconds > left:
                raise DeadlineExceeded(f'a {seconds:g} s wait would end past the run deadline ({max(left, 0):.1f} s left)')
            sleep(seconds)
        return bounded_sleep


class DeadlinePort:
    """VenuePort proxy: an opening market order past the deadline raises DeadlineExceeded before it is sent."""

    def __init__(self, venue, deadline):
        self._v, self._d = venue, deadline

    def __getattr__(self, name):
        return getattr(self._v, name)

    def submit_market(self, order):
        if not order.reduce and self._d.expired():
            raise DeadlineExceeded(f'opening order {order.ref.client_id}: the run deadline of {self._d.deadline_s:g} s '
                                   f'has passed; not sent')
        return self._v.submit_market(order)
