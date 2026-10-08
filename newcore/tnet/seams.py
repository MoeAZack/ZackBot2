"""Seams of the runner-driven harness.

BoundedPort   the VenuePort the Runner talks to: every submit is checked against the scenario's bound (max orders,
              max opening notional at the last closed price) and its client id is appended to the run ledger BEFORE it
              is sent. A breach raises BoundExceeded (the scenario FAILs, cleanup runs). Reads pass through.
              Deadline (second layer; the driver checks it before and after every candle wait): past the scenario's
              absolute deadline an OPENING order raises DeadlineExceeded before it is sent. A risk-REDUCING send (a
              protective stop, a reduce-only close) is still let through and ledgered past_deadline=True: refusing it
              would leave an open position naked until the teardown, which sends the same kind of order anyway.
PortFaults    FakeVenue-side faults on the next matching effect (entry / close / stop / cancel).
HttpFaults    the same faults on the testnet target, at the HTTP seam UNDER the transport (newcore.venue.tnet_seams.
              FaultHttp semantics), matched on the request: entry / close = POST /fapi/v1/order MARKET opening /
              closing (hedge mode: side vs positionSide), stop = POST STOP_MARKET or /fapi/v1/algoOrder, cancel =
              DELETE order / algoOrder. 'refuse' answers a SYNTHETIC Binance error without sending anything.
"""
import json
from decimal import Decimal
from urllib.parse import parse_qsl

from newcore.ports import venue as P
from newcore.venue.tnet_seams import FaultHttp
from newcore.venue.wire import HttpResponse

KINDS = ('lost_response', 'timeout', 'refuse')
ON = ('entry', 'close', 'stop', 'cancel')


class BoundExceeded(Exception):
    """The harness refused a submit: the scenario's order / notional bound would be exceeded (runaway guard)."""


class DeadlineExceeded(Exception):
    """The scenario's absolute wall deadline (bound.max_wall_s) has passed: nothing may OPEN exposure any more."""


class BoundedPort:
    def __init__(self, inner, *, max_orders, max_notional, price_of, ledger=None, expired=None):
        self.inner, self.max_orders, self.max_notional = inner, max_orders, Decimal(max_notional)
        self.price_of = price_of                       # symbol -> Decimal | None (the last closed candle's close)
        self.ledger = ledger if ledger is not None else []
        self.expired = expired                         # () -> bool: the scenario deadline has passed
        self.submits = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def _admit(self, kind, order, opening):
        late = self.expired is not None and self.expired()
        if late and opening:                           # second deadline layer, checked before EVERY send
            raise DeadlineExceeded(f'{kind} {order.ref.client_id}: the scenario deadline has passed')
        if self.submits + 1 > self.max_orders:
            raise BoundExceeded(f'{kind} {order.ref.client_id}: more than {self.max_orders} orders')
        if opening:
            px = self.price_of(order.ref.symbol)
            if px is None:
                raise BoundExceeded(f'{kind} {order.ref.client_id}: no reference price for the notional bound')
            if order.qty * px > self.max_notional:
                raise BoundExceeded(f'{kind} {order.ref.client_id}: notional {order.qty * px} > {self.max_notional}')
        self.submits += 1
        self.ledger.append({'kind': kind, 'symbol': order.ref.symbol, 'client_id': order.ref.client_id,
                            'route': order.ref.route, 'side': order.position_side, 'qty': str(order.qty),
                            'past_deadline': late})

    def submit_market(self, order):
        self._admit('close' if order.reduce else 'entry', order, not order.reduce)
        return self.inner.submit_market(order)

    def submit_stop(self, order):
        self._admit('stop', order, False)
        return self.inner.submit_stop(order)


def _check(on, kind, code):
    if on not in ON or kind not in KINDS:
        raise ValueError(f'bad fault {on!r} / {kind!r}')
    if (kind == 'refuse') != (code is not None):
        raise ValueError('code goes with (and only with) refuse')


class PortFaults:
    """FakeVenue port wrapper: arm(on, kind, code) applies once, to the next effect of that kind."""

    def __init__(self, inner):
        self.inner = inner
        self._armed = {}
        self.injected = []                             # (on, kind, client id)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def arm(self, on, kind, code=None):
        _check(on, kind, code)
        self._armed[on] = (kind, code)

    def _effect(self, on, ref, call):
        f = self._armed.pop(on, None)
        if f is None:
            return call()
        kind, code = f
        self.injected.append((on, kind, ref.client_id))
        now = self.inner.now_ms
        if kind == 'refuse':
            return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=now, error_code=code,
                                  detail='tnet_injected')
        if kind == 'lost_response':
            call()
        return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=now, detail='timeout')

    def submit_market(self, order):
        return self._effect('close' if order.reduce else 'entry', order.ref, lambda: self.inner.submit_market(order))

    def submit_stop(self, order):
        return self._effect('stop', order.ref, lambda: self.inner.submit_stop(order))

    def cancel(self, ref):
        return self._effect('cancel', ref, lambda: self.inner.cancel(ref))


def classify(request):
    """'entry' | 'close' | 'stop' | 'cancel' | None for one HttpRequest (hedge mode)."""
    path = request.url.split('binancefuture.com', 1)[-1].rstrip('/')
    q = dict(parse_qsl(request.query or ''))
    if request.method == 'DELETE' and path in ('/fapi/v1/order', '/fapi/v1/algoOrder'):
        return 'cancel'
    if request.method != 'POST':
        return None
    if path == '/fapi/v1/algoOrder':
        return 'stop'
    if path != '/fapi/v1/order':
        return None
    if q.get('type') == 'STOP_MARKET':
        return 'stop'
    if q.get('type') == 'MARKET':
        closing = (q.get('positionSide') == 'LONG') == (q.get('side') == 'SELL')
        return 'close' if closing else 'entry'
    return None


class HttpFaults:
    """The testnet target's seam: wrap the http callable (the sender, or a recorder around it)."""

    def __init__(self, inner):
        if not callable(inner):
            raise ValueError('inner must be the http callable')
        self._fh = FaultHttp(inner)
        self._inner = inner
        self._armed = {}
        self.injected = []                             # (on, kind, path)

    def arm(self, on, kind, code=None):
        _check(on, kind, code)
        self._armed[on] = (kind, code)

    def __call__(self, request):
        on = classify(request) if self._armed else None
        f = self._armed.pop(on, None) if on is not None else None
        if f is None:
            return self._inner(request)
        kind, code = f
        self.injected.append((on, kind, request.url.split('binancefuture.com', 1)[-1]))
        if kind == 'refuse':
            body = json.dumps({'code': code, 'msg': 'tnet synthetic refusal (not sent)'}).encode()
            return HttpResponse(400, {}, body)
        self._fh.arm(kind, 1)
        return self._fh(request)


class NoFaults:
    """A replay's seam: nothing is injected (the recorded cassette already holds the faults the transport saw); an
    armed fault is only noted."""

    def __init__(self, inner):
        if not callable(inner):
            raise ValueError('inner must be the http callable')
        self._inner = inner
        self.injected = []

    def arm(self, on, kind, code=None):
        _check(on, kind, code)
        self.injected.append((on, kind, 'replayed'))

    def __call__(self, request):
        return self._inner(request)


class AdoptedView:
    """N4: what the Runner and the final truth see of an account that holds ADOPTED foreign exposure (the preflight's
    --adopt-foreign): positions minus their adopted baseline, open orders without the adopted foreign client ids.
    Sends pass through unchanged (the Runner only ever sends for its own lots). Until REC-02 adoption is wired, this
    scopes the Runner's reconciliation to the exposure the scenario owns."""

    def __init__(self, inner, *, baseline=None, adopted_orders=()):
        self.inner = inner
        self.baseline = dict(baseline or {})
        self.adopted_orders = frozenset(adopted_orders)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def positions(self, symbol=None):
        import dataclasses
        r = self.inner.positions(symbol)
        if r.kind is not P.ReadKind.OK or not self.baseline:
            return r
        rows = tuple(dataclasses.replace(p, qty=max(p.qty - self.baseline.get((p.symbol, p.side), Decimal(0)),
                                                    Decimal(0))) for p in r.value)
        return dataclasses.replace(r, value=rows)

    def open_orders(self, symbol=None):
        import dataclasses
        r = self.inner.open_orders(symbol)
        if r.kind is not P.ReadKind.OK or not self.adopted_orders:
            return r
        return dataclasses.replace(r, value=tuple(o for o in r.value if o.ref.client_id not in self.adopted_orders))
