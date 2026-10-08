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
