"""Bounded Binance USD-M testnet adapter implementing the frozen VenuePort.

This module is deliberately incapable of targeting mainnet.  Each public port
call performs zero or one HTTP request through the injected sender, never
retries, never changes route, and turns ambiguous transport/venue evidence into
UNKNOWN.  The runner owns durable write-ahead intent state and recovery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable, Protocol
from urllib.parse import urlencode

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError, check_int, check_ms, check_symbol, req

TESTNET_BASE_URL = 'https://testnet.binancefuture.com'
_PREFIX = {'classic': 'zbn1o-', 'algo': 'zbn1a-'}
_OPENING = {'LONG': 'BUY', 'SHORT': 'SELL'}
_CLOSING = {'LONG': 'SELL', 'SHORT': 'BUY'}
_FINAL = frozenset({'FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH', 'REJECTED'})
_KNOWN = frozenset({'NEW', 'PARTIALLY_FILLED', 'PENDING_NEW'})
_ALGO_KNOWN = frozenset({'NEW', 'WORKING', 'PENDING_NEW'})
_ALGO_TRIGGERED = frozenset({'TRIGGERING', 'TRIGGERED', 'FINISHED'})
_ALGO_FINAL = frozenset({'CANCELED', 'EXPIRED', 'REJECTED'})
_NOT_FOUND = frozenset({-2011, -2013})
_AMBIGUOUS = frozenset({-1000, -1001, -1006, -1007})
_DUPLICATE = -4116
_ALGO_ROUTE = -4120
_MAX_RATE_VALUE = 2 ** 63 - 1


class VenueBindingError(ValueError):
    """The configured environment is not the exact Binance USD-M testnet."""


class CredentialSource(Protocol):
    def api_key(self) -> str: ...
    def sign(self, payload: bytes) -> str: ...


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    query: str
    headers: tuple[tuple[str, str], ...]
    timeout_s: float


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    retry_after_ms: int | None = None
    used_weight_1m: int | None = None


@dataclass(frozen=True, slots=True)
class RateEvidence:
    """Sanitized HTTP rate evidence; never contains headers, URLs or credentials."""

    http_status: int
    retry_after_ms: int | None
    used_weight_1m: int | None
    observed_at_ms: int

    def __post_init__(self):
        check_int(self.http_status, 'RateEvidence.http_status', 100, 599)
        if self.retry_after_ms is not None:
            check_int(self.retry_after_ms, 'RateEvidence.retry_after_ms', 0, _MAX_RATE_VALUE)
        if self.used_weight_1m is not None:
            check_int(self.used_weight_1m, 'RateEvidence.used_weight_1m', 0, _MAX_RATE_VALUE)
        check_ms(self.observed_at_ms, 'RateEvidence.observed_at_ms')


class RateObserver(Protocol):
    def __call__(self, evidence: RateEvidence) -> None: ...


class _Unknown(Exception):
    def __init__(self, detail: str, code: int | None = None):
        self.detail, self.code = detail, code


def _json_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _Unknown('malformed')
        out[key] = value
    return out


def _decimal(value, name: str, *, nonnegative: bool = True) -> Decimal:
    if type(value) is not str:
        raise _Unknown('malformed')
    try:
        out = Decimal(value)
    except InvalidOperation:
        raise _Unknown('malformed') from None
    if not out.is_finite() or (nonnegative and out < 0):
        raise _Unknown('malformed')
    return out


def _positive_id(value) -> str:
    if type(value) not in (str, int) or isinstance(value, bool):
        raise _Unknown('malformed')
    out = str(value)
    if not out.isascii() or not out.isdigit() or int(out) <= 0:
        raise _Unknown('malformed')
    return out


class BinanceUsdMTestnetVenue:
    """A fail-closed, injected-I/O VenuePort for Binance USD-M testnet."""

    __test__ = False

    def __init__(self, *, environment: str, base_url: str, sender: Callable[[HttpRequest], HttpResponse],
                 credentials: CredentialSource, clock: Callable[[], int], timeout_s: float = 10.0,
                 recv_window_ms: int = 5000, rate_observer: RateObserver | None = None):
        if type(environment) is not str or environment != 'testnet':
            raise VenueBindingError('environment must be the exact string testnet')
        if type(base_url) is not str or base_url != TESTNET_BASE_URL:
            raise VenueBindingError('base_url must equal the exact Binance USD-M testnet host')
        if not callable(sender) or not callable(clock):
            raise PortValueError('venue', 'callable sender and clock')
        if not callable(getattr(credentials, 'api_key', None)) or not callable(getattr(credentials, 'sign', None)):
            raise PortValueError('credentials', 'api_key() and sign(bytes)')
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 60:
            raise PortValueError('timeout_s', 'a number in (0, 60]')
        if type(recv_window_ms) is not int or not 1 <= recv_window_ms <= 10_000:
            raise PortValueError('recv_window_ms', 'an int in 1..10000')
        if rate_observer is not None and not callable(rate_observer):
            raise PortValueError('rate_observer', 'a callable or None')
        self._base_url = base_url
        self._sender, self._credentials, self._clock = sender, credentials, clock
        self._rate_observer = rate_observer
        self._timeout_s, self._recv_window_ms = float(timeout_s), recv_window_ms
        self._submitted: set[str] = set()

    def _now(self) -> int:
        return self._clock()

    @staticmethod
    def _owned(ref: P.OrderRef, route: str | None = None) -> None:
        req(isinstance(ref, P.OrderRef), 'ref', 'an OrderRef')
        req(K.is_newcore_client_id(ref.client_id) and ref.client_id.startswith(_PREFIX[ref.route]),
            'ref.client_id', 'a deterministic NEWCORE client id for this route')
        if route is not None:
            req(ref.route == route, 'ref.route', route)

    def _request(self, method: str, path: str, params: list[tuple[str, str]]) -> object:
        # Re-check the exact binding on every request so mutation cannot redirect traffic.
        if self._base_url != TESTNET_BASE_URL:
            raise _Unknown('binding_changed')
        timestamp = self._now()
        signed = params + [('recvWindow', str(self._recv_window_ms)), ('timestamp', str(timestamp))]
        payload = urlencode(signed)
        try:
            signature = self._credentials.sign(payload.encode('ascii'))
            api_key = self._credentials.api_key()
        except Exception:
            raise _Unknown('credentials') from None
        if type(signature) is not str or not signature or type(api_key) is not str or not api_key:
            raise _Unknown('credentials')
        query = payload + '&signature=' + signature
        request = HttpRequest(method, self._base_url + path, query, (('X-MBX-APIKEY', api_key),), self._timeout_s)
        try:
            response = self._sender(request)
        except (TimeoutError, OSError):
            raise _Unknown('timeout') from None
        except Exception:
            raise _Unknown('transport_error') from None
        if (not isinstance(response, HttpResponse) or type(response.status) is not int
                or not 100 <= response.status <= 599 or type(response.body) is not bytes
                or (response.retry_after_ms is not None
                    and (type(response.retry_after_ms) is not int
                         or not 0 <= response.retry_after_ms <= _MAX_RATE_VALUE))
                or (response.used_weight_1m is not None
                    and (type(response.used_weight_1m) is not int
                         or not 0 <= response.used_weight_1m <= _MAX_RATE_VALUE))):
            raise _Unknown('bad_http_response')
        if self._rate_observer is not None:
            try:
                self._rate_observer(RateEvidence(response.status, response.retry_after_ms,
                                                 response.used_weight_1m, self._now()))
            except Exception:
                raise _Unknown('rate_observer') from None
        if response.status >= 500:
            raise _Unknown('http_5xx')
        try:
            data = json.loads(response.body.decode('utf-8'), object_pairs_hook=_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise _Unknown('malformed') from None
        if isinstance(data, dict) and 'code' in data:
            code = data['code']
            if type(code) is not int or code >= 0:
                raise _Unknown('malformed')
            if code == _DUPLICATE:
                raise _Unknown('duplicate_cid', code)
            if code in _AMBIGUOUS:
                raise _Unknown('ambiguous_code', code)
            return _VenueError(code)
        if 400 <= response.status < 500:
            raise _Unknown('malformed')
        if not 200 <= response.status < 300:
            raise _Unknown('unexpected_status')
        if not isinstance(data, (dict, list)):
            raise _Unknown('malformed')
        return data

    def _order_call(self, ref: P.OrderRef, method: str, path: str, params: list[tuple[str, str]]) -> P.OrderOutcome:
        now = self._now()
        try:
            data = self._request(method, path, params)
            if isinstance(data, _VenueError):
                if data.code in _NOT_FOUND:
                    return P.OrderOutcome(kind=P.OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=now,
                                          error_code=data.code, detail='venue_not_found')
                detail = 'algo_route' if data.code == _ALGO_ROUTE else 'venue_rejected'
                return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, ref=ref, observed_at_ms=now,
                                      error_code=data.code, detail=detail)
            return self._order_record(ref, data, now)
        except _Unknown as ex:
            return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=now,
                                  error_code=ex.code, detail=ex.detail)

    @staticmethod
    def _order_record(ref: P.OrderRef, data: object, now: int) -> P.OrderOutcome:
        if not isinstance(data, dict):
            raise _Unknown('malformed')
        echoed_symbol = data.get('symbol')
        echoed_cid = data.get('clientOrderId', data.get('clientAlgoId'))
        if echoed_symbol != ref.symbol or echoed_cid != ref.client_id:
            raise _Unknown('echo_mismatch')
        status = data.get('status', data.get('algoStatus'))
        if status is None and ('orderId' in data or 'algoId' in data):
            return P.OrderOutcome(kind=P.OutcomeKind.ACKNOWLEDGED, ref=ref, observed_at_ms=now)
        if type(status) is not str:
            raise _Unknown('malformed')
        if ref.route == 'algo':
            algo_id = _positive_id(data.get('algoId'))
            if status in _ALGO_TRIGGERED:
                child = data.get('actualOrderId')
                if child is None:
                    raise _Unknown('algo_triggered_no_child')
                return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, ref=ref, observed_at_ms=now, status=status,
                                      exchange_order_id=_positive_id(child), detail='algo_triggered')
            if status in _ALGO_KNOWN:
                return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, ref=ref, observed_at_ms=now, status=status,
                                      exchange_order_id=algo_id)
            if status in _ALGO_FINAL:
                return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=ref, observed_at_ms=now, status=status,
                                      exchange_order_id=algo_id, executed_qty=Decimal('0'))
            raise _Unknown('unknown_status')
        exchange_id = _positive_id(data.get('orderId', data.get('algoId')))
        if status in _KNOWN:
            return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, ref=ref, observed_at_ms=now, status=status,
                                  exchange_order_id=exchange_id)
        if status not in _FINAL:
            raise _Unknown('unknown_status')
        executed = _decimal(data.get('executedQty', '0'), 'executedQty')
        avg = _decimal(data.get('avgPrice'), 'avgPrice') if executed > 0 else None
        if executed > 0 and (avg is None or avg <= 0):
            raise _Unknown('final_without_price')
        return P.OrderOutcome(kind=P.OutcomeKind.FINAL, ref=ref, observed_at_ms=now, status=status,
                              exchange_order_id=exchange_id, executed_qty=executed, avg_price=avg)

    def _claim_submit(self, ref: P.OrderRef) -> P.OrderOutcome | None:
        if ref.client_id in self._submitted:
            return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self._now(),
                                  detail='duplicate_cid')
        self._submitted.add(ref.client_id)
        return None

    def submit_market(self, order: P.MarketOrder) -> P.OrderOutcome:
        req(isinstance(order, P.MarketOrder), 'order', 'a MarketOrder')
        self._owned(order.ref, 'classic')
        duplicate = self._claim_submit(order.ref)
        if duplicate is not None:
            return duplicate
        side = _CLOSING[order.position_side] if order.reduce else _OPENING[order.position_side]
        params = [('symbol', order.ref.symbol), ('side', side), ('positionSide', order.position_side),
                  ('type', 'MARKET'), ('quantity', format(order.qty, 'f')),
                  ('newClientOrderId', order.ref.client_id)]
        return self._order_call(order.ref, 'POST', '/fapi/v1/order', params)

    def submit_stop(self, order: P.StopOrder) -> P.OrderOutcome:
        req(isinstance(order, P.StopOrder), 'order', 'a StopOrder')
        self._owned(order.ref)
        duplicate = self._claim_submit(order.ref)
        if duplicate is not None:
            return duplicate
        side = _CLOSING[order.position_side]
        if order.ref.route == 'classic':
            path = '/fapi/v1/order'
            params = [('symbol', order.ref.symbol), ('side', side), ('positionSide', order.position_side),
                      ('type', 'STOP_MARKET'), ('quantity', format(order.qty, 'f')),
                      ('stopPrice', format(order.stop_price, 'f')), ('workingType', 'MARK_PRICE'),
                      ('newClientOrderId', order.ref.client_id)]
        else:
            path = '/fapi/v1/algoOrder'
            params = [('algoType', 'CONDITIONAL'), ('symbol', order.ref.symbol), ('side', side),
                      ('positionSide', order.position_side), ('type', 'STOP_MARKET'),
                      ('quantity', format(order.qty, 'f')), ('triggerPrice', format(order.stop_price, 'f')),
                      ('workingType', 'MARK_PRICE'), ('clientAlgoId', order.ref.client_id)]
        return self._order_call(order.ref, 'POST', path, params)

    def cancel(self, ref: P.OrderRef) -> P.OrderOutcome:
        self._owned(ref)
        if ref.route == 'classic':
            return self._order_call(ref, 'DELETE', '/fapi/v1/order',
                                    [('symbol', ref.symbol), ('origClientOrderId', ref.client_id)])
        return self._order_call(ref, 'DELETE', '/fapi/v1/algoOrder', [('clientAlgoId', ref.client_id)])

    def query(self, ref: P.OrderRef) -> P.OrderOutcome:
        self._owned(ref)
        if ref.route == 'classic':
            return self._order_call(ref, 'GET', '/fapi/v1/order',
                                    [('symbol', ref.symbol), ('origClientOrderId', ref.client_id)])
        return self._order_call(ref, 'GET', '/fapi/v1/algoOrder', [('clientAlgoId', ref.client_id)])

    def _read(self, path: str, params: list[tuple[str, str]], convert) -> P.ReadOutcome:
        now = self._now()
        try:
            data = self._request('GET', path, params)
            if isinstance(data, _VenueError):
                return P.ReadOutcome(kind=P.ReadKind.REJECTED, observed_at_ms=now, error_code=data.code,
                                     detail='venue_rejected')
            return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=now, value=convert(data))
        except (_Unknown, PortValueError, ValueError, TypeError, KeyError) as ex:
            detail = ex.detail if isinstance(ex, _Unknown) else 'malformed'
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now, detail=detail)

    def positions(self, symbol: str | None = None) -> P.ReadOutcome:
        if symbol is not None:
            check_symbol(symbol, 'positions.symbol')

        def convert(data):
            if not isinstance(data, list):
                raise _Unknown('malformed')
            out = []
            for row in data:
                if not isinstance(row, dict) or row.get('positionSide') not in ('LONG', 'SHORT'):
                    raise _Unknown('malformed')
                if symbol is not None and row.get('symbol') != symbol:
                    raise _Unknown('echo_mismatch')
                qty = abs(_decimal(row.get('positionAmt'), 'positionAmt', nonnegative=False))
                out.append(P.VenuePosition(symbol=row['symbol'], side=row['positionSide'], qty=qty,
                                           entry_price=_decimal(row.get('entryPrice'), 'entryPrice')))
            return tuple(out)
        params = [] if symbol is None else [('symbol', symbol)]
        return self._read('/fapi/v2/positionRisk', params, convert)

    def open_orders(self, symbol: str | None = None) -> P.ReadOutcome:
        if symbol is not None:
            check_symbol(symbol, 'open_orders.symbol')
        # Binance exposes classic and algo books separately.  A one-request implementation cannot prove the combined
        # frozen-port value, so fail closed rather than return a trusted partial/empty book.
        return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='split_route_read')

    def fills(self, symbol: str, exchange_order_id: str) -> P.ReadOutcome:
        check_symbol(symbol, 'fills.symbol')
        order_id = _positive_id(exchange_order_id)

        def convert(data):
            if not isinstance(data, list):
                raise _Unknown('malformed')
            out, seen = [], set()
            for row in data:
                if not isinstance(row, dict) or row.get('symbol') != symbol or str(row.get('orderId')) != order_id:
                    raise _Unknown('echo_mismatch')
                trade_id = _positive_id(row.get('id'))
                if trade_id in seen:
                    raise _Unknown('duplicate_cid')
                seen.add(trade_id)
                ps = row.get('positionSide')
                if ps not in ('LONG', 'SHORT') or type(row.get('maker')) is not bool or type(row.get('commissionAsset')) is not str:
                    raise _Unknown('malformed')
                out.append(P.VenueFill(trade_id=trade_id, exchange_order_id=order_id, symbol=symbol,
                                       position_side=ps, qty=_decimal(row.get('qty'), 'qty'),
                                       price=_decimal(row.get('price'), 'price'),
                                       fee=_decimal(row.get('commission'), 'commission', nonnegative=False),
                                       fee_asset=row['commissionAsset'],
                                       realized_pnl=_decimal(row.get('realizedPnl'), 'realizedPnl', nonnegative=False),
                                       maker=row['maker'], at_ms=row.get('time')))
            return tuple(sorted(out, key=lambda f: (f.at_ms, f.trade_id)))
        return self._read('/fapi/v1/userTrades', [('symbol', symbol), ('orderId', order_id), ('limit', '1000')], convert)


@dataclass(frozen=True, slots=True)
class _VenueError:
    code: int


__all__ = ['TESTNET_BASE_URL', 'BinanceUsdMTestnetVenue', 'CredentialSource', 'HttpRequest', 'HttpResponse',
           'RateEvidence', 'RateObserver', 'VenueBindingError']
