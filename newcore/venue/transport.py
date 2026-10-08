"""BinanceTestnetTransport: the raw wire layer below the (separately frozen) VenuePort / TestnetVenue adapter.

One public call = at most one HTTP request through the injected `http` function. No retry, no sleep, no clock read
except the injected `clock()` (integer ms) used for SIGNED timestamps, no environment read, no credentials at import.
Every answer becomes a typed OrderOutcome / ReadOutcome (see outcomes.py); a transport failure is never "failed",
it is UNKNOWN.

Position mode must be declared (it changes the wire shape of every order):
- HEDGE   (dualSidePosition=true, NC-01 default): positionSide=LONG|SHORT is sent; reduceOnly is NEVER sent (Binance
          answers -1106 if it is). A closing order is the opposite side on the same positionSide, which Binance only
          lets reduce that side.
- ONE_WAY (dualSidePosition=false): no positionSide (Binance uses BOTH); closing orders carry reduceOnly=true.
Either way the caller states reduce_only, and it must agree with the side (closing side <=> reduce_only).
"""
import logging
import re
from decimal import Decimal
from enum import Enum

from . import records as R
from .credentials import CredentialsUnavailable, key_digest as _key_digest
from .errors import (ALGO_FALLBACK_CODES, ErrorCategory, NotFoundEvidence, NotFoundEvidenceType, VenueError,
                     categorize, scrub_text)
from .guard import TESTNET_BASE_URL, VenueGuardError, check_binding, check_request_url
from .outcomes import OrderOutcome, OrderOutcomeKind, ReadKind, ReadOutcome
from .signing import check_ms, check_recv_window, encode_params, signed_query
from .wire import (API_KEY_HEADER, HttpRequest, HttpResponse, RateLimitUsage, WireConnectionError,
                   WireResponseTooLarge, WireSeamError, WireTimeout, parse_rate_limits)

log = logging.getLogger('newcore.venue.transport')


class VenueInputError(ValueError):
    """The caller's arguments are invalid. Raised BEFORE any request is built; nothing was sent."""


class PositionMode(Enum):
    HEDGE = 'hedge'
    ONE_WAY = 'one_way'


class StopRoute(Enum):
    CLASSIC = 'classic'     # POST /fapi/v1/order type=STOP_MARKET (newClientOrderId)
    ALGO = 'algo'           # POST /fapi/v1/algoOrder algoType=CONDITIONAL (clientAlgoId)


CLIENT_ID_RE = re.compile(r'^[.A-Z:/a-z0-9_-]{1,36}$')        # Binance newClientOrderId / clientAlgoId grammar
SYMBOL_RE = re.compile(r'^[A-Z0-9]{2,30}$')                 # NC-01 symbol length (Codex ruling C1)
INTERVALS = ('1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M')
CLOSING_SIDE = {'LONG': 'SELL', 'SHORT': 'BUY'}
OPENING_SIDE = {'LONG': 'BUY', 'SHORT': 'SELL'}
SEVEN_DAYS_MS = 7 * 24 * 3600 * 1000
_NOT_FOUND_TEXT = ('not exist', 'unknown order', 'not found')


# ---------- argument validators (raise VenueInputError; values are echoed only when they are not secrets) ----------

def _symbol(v):
    if type(v) is not str or not SYMBOL_RE.fullmatch(v):
        raise VenueInputError('symbol must be upper-case alphanumeric (2..30)')
    return v


def _cid(v, what='client_id'):
    if type(v) is not str or not CLIENT_ID_RE.fullmatch(v):
        raise VenueInputError(f'{what} must match {CLIENT_ID_RE.pattern}')
    return v


def _qty(v, what):
    if type(v) is not Decimal or not v.is_finite() or v <= 0:
        raise VenueInputError(f'{what} must be a finite positive Decimal')
    s = format(v.normalize(), 'f')
    if not re.fullmatch(r'^[0-9]+(\.[0-9]+)?$', s):
        raise VenueInputError(f'{what} does not format as a plain decimal')
    return s


def _choice(v, allowed, what):
    if type(v) is not str or v not in allowed:
        raise VenueInputError(f'{what} must be one of {allowed}')
    return v


def _opt_ms(v, what):
    if v is None:
        return None
    try:
        return str(check_ms(v, what))
    except ValueError as ex:
        raise VenueInputError(str(ex)) from None


def _opt_limit(v, hi):
    if v is None:
        return None
    if type(v) is not int or not 1 <= v <= hi:
        raise VenueInputError(f'limit must be an int in 1..{hi}')
    return str(v)


def _pairs(*items):
    return [(k, v) for k, v in items if v is not None]


class BinanceTestnetTransport:
    __slots__ = ('_environment', '_base_url', '_http', '_clock', '_credentials', '_mode', '_recv_window', '_timeout',
                 '_scrubber')

    def __init__(self, *, environment, http, clock, position_mode, credentials=None,
                 base_url=TESTNET_BASE_URL, recv_window_ms=5000, timeout_s=10.0, scrubber=None):
        """scrubber: optional object with scrub(text) -> str (e.g. the SecretScrubber holding the key and secret);
        venue-provided error text passes through it, and the API key in use is always redacted by value."""
        if scrubber is not None and not callable(getattr(scrubber, 'scrub', None)):
            raise VenueInputError('scrubber must provide scrub(text) -> str')
        self._scrubber = scrubber
        self._environment, self._base_url = check_binding(environment, base_url)     # raises VenueGuardError
        if not callable(http):
            raise VenueInputError('http must be callable(HttpRequest) -> HttpResponse')
        if not callable(clock):
            raise VenueInputError('clock must be callable() -> int ms')
        if not isinstance(position_mode, PositionMode):
            raise VenueInputError('position_mode must be a PositionMode')
        if credentials is not None and not (callable(getattr(credentials, 'api_key', None))
                                            and callable(getattr(credentials, 'sign', None))):
            raise VenueInputError('credentials must implement CredentialSource (api_key(), sign(bytes))')
        try:
            check_recv_window(recv_window_ms)
        except ValueError as ex:
            raise VenueInputError(str(ex)) from None
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 60:
            raise VenueInputError('timeout_s must be a number in (0, 60]')
        self._http, self._clock, self._credentials = http, clock, credentials
        self._mode, self._recv_window, self._timeout = position_mode, recv_window_ms, float(timeout_s)

    def __repr__(self):
        return (f'BinanceTestnetTransport(environment={self._environment!r}, base_url={self._base_url!r}, '
                f'position_mode={self._mode.name}, credentials={"present" if self._credentials else "absent"})')

    __str__ = __repr__

    @property
    def environment(self):
        return self._environment

    @property
    def base_url(self):
        return self._base_url

    @property
    def position_mode(self):
        return self._mode

    def key_digest(self):
        """One-way fingerprint of the API key in use. It identifies a CREDENTIAL, not an exchange account: the NEWCORE
        AccountId binding is provisioned separately and a rotated key keeps the same AccountId."""
        return _key_digest(self._api_key())

    # ---------- request plumbing ----------

    def _api_key(self):
        if self._credentials is None:
            raise CredentialsUnavailable('no credential source configured (signed endpoints need one)')
        try:
            key = self._credentials.api_key()
        except Exception:
            raise CredentialsUnavailable('credential source failed to provide the API key') from None
        if type(key) is not str or not key:
            raise CredentialsUnavailable('credential source returned no API key')
        return key

    def _build(self, method, path, pairs, signed):
        url = check_request_url(self._base_url + path)
        if not signed:
            return HttpRequest(method, url, encode_params(pairs), (), self._timeout, False)
        self._api_key()                 # fail fast (CredentialsUnavailable) before anything is signed or sent
        now = self._clock()

        def sign(payload):
            try:
                return self._credentials.sign(payload)
            except Exception:
                raise CredentialsUnavailable('credential source failed to sign') from None
        try:
            query, sig = signed_query(pairs, now, self._recv_window, sign)
        except CredentialsUnavailable:
            raise
        except ValueError as ex:
            raise VenueInputError(str(ex)) from None
        # The raw key is NOT put into the request: the sender fetches it from the provider at send time (finding 5).
        return HttpRequest(method, url, query + '&signature=' + sig, ((API_KEY_HEADER, '<redacted>'),), self._timeout,
                           True, key_provider=self._api_key)

    def _exchange(self, method, path, pairs, signed):
        """Send one request. Returns (verdict, payload, http_status, rate) where verdict is 'ok' (payload = decoded
        JSON), 'error' (payload = VenueError, a clean refusal) or 'unknown' (payload = (reason token, VenueError|None))."""
        req = self._build(method, path, pairs, signed)
        try:
            resp = self._http(req)
        except (VenueGuardError, WireSeamError, CredentialsUnavailable):
            raise                              # the seam refused before sending / a broken harness: not venue data
        except WireTimeout:
            return 'unknown', ('timeout', None), None, RateLimitUsage()
        except WireResponseTooLarge:
            return 'unknown', ('response_too_large', None), None, RateLimitUsage()
        except WireConnectionError:
            return 'unknown', ('connection', None), None, RateLimitUsage()
        except Exception:                      # message deliberately dropped: it may contain the signed URL
            return 'unknown', ('transport_error', None), None, RateLimitUsage()
        if not isinstance(resp, HttpResponse) or type(resp.status) is not int:
            return 'unknown', ('bad_http_response', None), None, RateLimitUsage()
        rate = parse_rate_limits(resp.headers)
        st = resp.status
        if st >= 500:
            return 'unknown', ('http_5xx', self._error_from(st, resp.body)), st, rate
        if not (200 <= st < 300 or 400 <= st < 500):
            return 'unknown', ('http_status_unexpected', None), st, rate
        try:
            data = R.decode_json(resp.body) if resp.body else None
        except R.MalformedResponse:
            data = R.MalformedResponse
        if 400 <= st < 500:
            if isinstance(data, dict) and _code(data) is not None and isinstance(_code(data), int) and _code(data) < 0:
                err = self._venue_error(st, _code(data), data.get('msg', ''))
                if err.category is ErrorCategory.AMBIGUOUS:
                    return 'unknown', ('ambiguous_code', err), st, rate
                return 'error', err, st, rate
            if st == 404:
                return 'error', VenueError(st, None, 'HTTP_404', ErrorCategory.ENDPOINT_UNSUPPORTED, ''), st, rate
            if st in (418, 429):
                return 'error', VenueError(st, None, f'HTTP_{st}', ErrorCategory.RATE_LIMIT, ''), st, rate
            return 'unknown', ('unreadable_error_body', None), st, rate
        # 2xx
        if data is R.MalformedResponse or data is None:
            return 'unknown', ('unreadable_body', None), st, rate
        if isinstance(data, dict) and 'code' in data:
            c = _code(data)
            if c in (0, 200):
                return 'ok', data, st, rate
            if isinstance(c, int) and c < 0:
                err = self._venue_error(st, c, data.get('msg', ''))
                if err.category is ErrorCategory.AMBIGUOUS:
                    return 'unknown', ('ambiguous_code', err), st, rate
                return 'error', err, st, rate
            return 'unknown', ('unexpected_code', None), st, rate
        return 'ok', data, st, rate

    def _error_from(self, status, body):
        try:
            data = R.decode_json(body) if body else None
        except R.MalformedResponse:
            return None
        if isinstance(data, dict) and isinstance(_code(data), int):
            return self._venue_error(status, _code(data), data.get('msg', ''))
        return None

    def _venue_error(self, status, code, msg):
        """A VenueError whose message is scrubbed by value (the API key in use, case / percent / separator
        insensitive), by the optional scrubber, and by token shape."""
        values = ()
        if self._credentials is not None:
            try:
                key = self._credentials.api_key()
                if isinstance(key, str) and len(key) >= 8:
                    values = (key,)
            except Exception:
                values = ()
        name, cat = categorize(code)
        text = scrub_text(msg if isinstance(msg, str) else '', values, self._scrubber)
        return VenueError(status, code, name, cat, text)

    def _read(self, op, method, path, pairs, signed, parser):
        verdict, payload, st, rate = self._exchange(method, path, pairs, signed)
        if verdict == 'ok':
            try:
                value = parser(payload)
            except R.MalformedResponse:
                out = ReadOutcome(ReadKind.UNKNOWN, op, unknown_reason='malformed', http_status=st, rate=rate)
            else:
                out = ReadOutcome(ReadKind.OK, op, value=value, http_status=st, rate=rate)
        elif verdict == 'error':
            out = ReadOutcome(ReadKind.REJECTED, op, error=payload, http_status=st, rate=rate)
        else:
            out = ReadOutcome(ReadKind.UNKNOWN, op, error=payload[1], unknown_reason=payload[0], http_status=st,
                              rate=rate)
        log.debug('%s %s -> %s status=%s code=%s weight1m=%s', method, path, out.kind.value, st,
                  out.error.code if out.error else None, rate.weight('1m'))
        return out

    def _order_call(self, op, endpoint, cid, method, path, pairs, *, parse, echo_field, nf_types=None,
                    allow_ack=False, algo_route_hint=False):
        verdict, payload, st, rate = self._exchange(method, path, pairs, True)
        base = dict(operation=op, endpoint=endpoint, client_id=cid, http_status=st, rate=rate)
        if verdict == 'unknown':
            out = OrderOutcome(OrderOutcomeKind.UNKNOWN, error=payload[1], unknown_reason=payload[0], **base)
        elif verdict == 'error':
            out = self._refusal(payload, endpoint, cid, nf_types, algo_route_hint, base)
        elif allow_ack and isinstance(payload, dict) and 'algoStatus' not in payload:
            echoed = payload.get('clientAlgoId')
            if echoed is not None and echoed != cid:
                out = OrderOutcome(OrderOutcomeKind.UNKNOWN, unknown_reason='echo_mismatch', **base)
            elif _code(payload) == 200 and str(payload.get('msg', '')).lower() == 'success':
                out = OrderOutcome(OrderOutcomeKind.ACKNOWLEDGED, **base)
            else:
                out = OrderOutcome(OrderOutcomeKind.UNKNOWN, unknown_reason='malformed', **base)
        else:
            try:
                rec = parse(payload)
            except R.MalformedResponse:
                out = OrderOutcome(OrderOutcomeKind.UNKNOWN, unknown_reason='malformed', **base)
            else:
                if getattr(rec, echo_field) != cid or (pairs_symbol(pairs) not in (None, rec.symbol)):
                    out = OrderOutcome(OrderOutcomeKind.UNKNOWN, unknown_reason='echo_mismatch', **base)
                else:
                    kind = OrderOutcomeKind.FINAL if rec.is_final else OrderOutcomeKind.KNOWN
                    out = OrderOutcome(kind, record=rec, **base)
        log.debug('%s %s -> %s status=%s code=%s weight1m=%s', method, path, out.kind.value, st,
                  out.error.code if out.error else None, rate.weight('1m'))
        return out

    @staticmethod
    def _refusal(err, endpoint, cid, nf_types, algo_route_hint, base):
        if nf_types is not None:
            op_kind = 'cancel' if base['operation'].startswith('cancel') else 'query'
            lookup = 'clientAlgoId' if endpoint == 'algo' else 'origClientOrderId'
            ev_type = nf_types.get(err.code)
            matched_by = 'code'
            if ev_type is None and endpoint == 'algo' and err.category in (
                    ErrorCategory.UNMAPPED, ErrorCategory.VENUE_RULE, ErrorCategory.ORDER_REJECTED) \
                    and any(t in err.msg.lower() for t in _NOT_FOUND_TEXT):
                ev_type, matched_by = nf_types.get('message'), 'message'
            if ev_type is not None:
                ev = NotFoundEvidence(ev_type, endpoint, op_kind, lookup, cid, err.code, matched_by, err.msg)
                return OrderOutcome(OrderOutcomeKind.NOT_FOUND, error=err, not_found=ev, **base)
        if algo_route_hint and err.code in ALGO_FALLBACK_CODES:
            err = VenueError(err.http_status, err.code, err.name, err.category, err.msg, suggests_algo_route=True)
        return OrderOutcome(OrderOutcomeKind.REJECTED, error=err, **base)

    def _side_fields(self, position_side, side, reduce_only):
        """Hedge/one-way wire fields for an order on a position side; enforces closing side <=> reduce_only."""
        if type(reduce_only) is not bool:
            raise VenueInputError('reduce_only must be a bool')
        closing = side == CLOSING_SIDE[position_side]
        if closing != reduce_only:
            raise VenueInputError(f'side {side} on a {position_side} position is '
                                  f'{"a closing" if closing else "an opening"} order: reduce_only must be {closing}')
        if self._mode is PositionMode.HEDGE:
            return [('positionSide', position_side)]
        return [('reduceOnly', 'true')] if reduce_only else []

    # ---------- public market data (unsigned) ----------

    def server_time(self):
        return self._read('server_time', 'GET', '/fapi/v1/time', [], False, R.parse_server_time)

    def exchange_info(self):
        return self._read('exchange_info', 'GET', '/fapi/v1/exchangeInfo', [], False, R.parse_exchange_info)

    def klines(self, symbol, interval, *, start_ms=None, end_ms=None, limit=500):
        pairs = _pairs(('symbol', _symbol(symbol)), ('interval', _choice(interval, INTERVALS, 'interval')),
                       ('startTime', _opt_ms(start_ms, 'start_ms')), ('endTime', _opt_ms(end_ms, 'end_ms')),
                       ('limit', _opt_limit(limit, 1500)))
        if start_ms is not None and end_ms is not None and end_ms < start_ms:
            raise VenueInputError('end_ms before start_ms')
        return self._read('klines', 'GET', '/fapi/v1/klines', pairs, False, R.parse_klines)

    # ---------- account (signed reads) ----------

    def account(self):
        return self._read('account', 'GET', '/fapi/v2/account', [], True, R.parse_account)

    def positions(self, symbol=None):
        pairs = _pairs(('symbol', _symbol(symbol) if symbol is not None else None))
        return self._read('positions', 'GET', '/fapi/v2/positionRisk', pairs, True, R.parse_positions)

    def dual_side_position(self):
        """True = hedge mode on the account. The adapter compares it with the declared PositionMode."""
        return self._read('dual_side_position', 'GET', '/fapi/v1/positionSide/dual', [], True, R.parse_dual_side)

    def open_orders(self, symbol=None):
        pairs = _pairs(('symbol', _symbol(symbol) if symbol is not None else None))
        return self._read('open_orders', 'GET', '/fapi/v1/openOrders', pairs, True, R.parse_open_orders)

    def open_algo_orders(self, symbol=None):
        """Open conditional (algo) orders. A failed or unreadable answer is UNKNOWN, never "no algo stops"."""
        pairs = _pairs(('symbol', _symbol(symbol) if symbol is not None else None))
        return self._read('open_algo_orders', 'GET', '/fapi/v1/openAlgoOrders', pairs, True,
                          R.parse_open_algo_orders)

    def user_trades(self, symbol, *, order_id=None, start_ms=None, end_ms=None, from_id=None, limit=None):
        if order_id is not None and (type(order_id) is not int or order_id <= 0):
            raise VenueInputError('order_id must be a positive int')
        if from_id is not None and (type(from_id) is not int or from_id <= 0):
            raise VenueInputError('from_id must be a positive int')
        pairs = _pairs(('symbol', _symbol(symbol)), ('orderId', str(order_id) if order_id is not None else None),
                       ('startTime', _opt_ms(start_ms, 'start_ms')), ('endTime', _opt_ms(end_ms, 'end_ms')),
                       ('fromId', str(from_id) if from_id is not None else None), ('limit', _opt_limit(limit, 1000)))
        if start_ms is not None and end_ms is not None and not 0 <= end_ms - start_ms <= SEVEN_DAYS_MS:
            raise VenueInputError('start_ms..end_ms must be ordered and at most 7 days apart')
        return self._read('user_trades', 'GET', '/fapi/v1/userTrades', pairs, True, R.parse_fills)

    def income(self, *, symbol=None, income_type=None, start_ms=None, end_ms=None, limit=None):
        """One page of /fapi/v1/income (funding fees, commission, realized pnl, ...). Paginate with
        newcore.venue.income.income_history, which walks time windows and never returns a partial history as whole."""
        if income_type is not None and (type(income_type) is not str or not R.INCOME_TYPE_RE.fullmatch(income_type)):
            raise VenueInputError('income_type must be an upper-case Binance income type')
        pairs = _pairs(('symbol', _symbol(symbol) if symbol is not None else None), ('incomeType', income_type),
                       ('startTime', _opt_ms(start_ms, 'start_ms')), ('endTime', _opt_ms(end_ms, 'end_ms')),
                       ('limit', _opt_limit(limit, 1000)))
        if start_ms is not None and end_ms is not None and end_ms < start_ms:
            raise VenueInputError('end_ms before start_ms')
        return self._read('income', 'GET', '/fapi/v1/income', pairs, True, R.parse_income)

    # ---------- orders ----------

    def place_market(self, symbol, side, position_side, quantity, client_id, *, reduce_only):
        """MARKET order with the caller's deterministic newClientOrderId (passed through verbatim)."""
        symbol, cid = _symbol(symbol), _cid(client_id)
        side = _choice(side, ('BUY', 'SELL'), 'side')
        position_side = _choice(position_side, ('LONG', 'SHORT'), 'position_side')
        qty = _qty(quantity, 'quantity')
        pairs = [('symbol', symbol), ('side', side)] + self._side_fields(position_side, side, reduce_only) + [
            ('type', 'MARKET'), ('quantity', qty), ('newClientOrderId', cid), ('newOrderRespType', 'RESULT')]
        return self._order_call('place_market', 'classic', cid, 'POST', '/fapi/v1/order', pairs,
                                parse=R.parse_order, echo_field='client_order_id')

    def place_stop_market(self, symbol, position_side, quantity, stop_price, client_id, *, route):
        """Reduce-only protective STOP_MARKET (mark price) for `quantity` of the position side, with the caller's
        deterministic client id. route=CLASSIC uses newClientOrderId on /fapi/v1/order; route=ALGO uses clientAlgoId
        on /fapi/v1/algoOrder. No automatic fallback: a classic refusal with a code in ALGO_FALLBACK_CODES comes back
        REJECTED with error.suggests_algo_route=True and the caller re-sends on the algo route."""
        symbol, cid = _symbol(symbol), _cid(client_id)
        position_side = _choice(position_side, ('LONG', 'SHORT'), 'position_side')
        if not isinstance(route, StopRoute):
            raise VenueInputError('route must be a StopRoute')
        qty, trigger = _qty(quantity, 'quantity'), _qty(stop_price, 'stop_price')
        side = CLOSING_SIDE[position_side]
        fields = self._side_fields(position_side, side, True)
        if route is StopRoute.CLASSIC:
            pairs = [('symbol', symbol), ('side', side)] + fields + [
                ('type', 'STOP_MARKET'), ('quantity', qty), ('stopPrice', trigger), ('workingType', 'MARK_PRICE'),
                ('newClientOrderId', cid), ('newOrderRespType', 'RESULT')]
            return self._order_call('place_stop_market', 'classic', cid, 'POST', '/fapi/v1/order', pairs,
                                    parse=R.parse_order, echo_field='client_order_id', algo_route_hint=True)
        pairs = [('algoType', 'CONDITIONAL'), ('symbol', symbol), ('side', side)] + fields + [
            ('type', 'STOP_MARKET'), ('quantity', qty), ('triggerPrice', trigger), ('workingType', 'MARK_PRICE'),
            ('clientAlgoId', cid)]
        return self._order_call('place_stop_market_algo', 'algo', cid, 'POST', '/fapi/v1/algoOrder', pairs,
                                parse=R.parse_algo_order, echo_field='client_algo_id')

    def query_order(self, symbol, client_id):
        symbol, cid = _symbol(symbol), _cid(client_id)
        return self._order_call('query', 'classic', cid, 'GET', '/fapi/v1/order',
                                [('symbol', symbol), ('origClientOrderId', cid)], parse=R.parse_order,
                                echo_field='client_order_id',
                                nf_types={-2013: NotFoundEvidenceType.QUERY_NO_SUCH_ORDER})

    def query_algo_order(self, client_algo_id):
        cid = _cid(client_algo_id, 'client_algo_id')
        return self._order_call('query_algo', 'algo', cid, 'GET', '/fapi/v1/algoOrder', [('clientAlgoId', cid)],
                                parse=R.parse_algo_order, echo_field='client_algo_id',
                                nf_types={-2013: NotFoundEvidenceType.ALGO_QUERY_NOT_FOUND,
                                          'message': NotFoundEvidenceType.ALGO_QUERY_NOT_FOUND})

    def cancel_order(self, symbol, client_id):
        symbol, cid = _symbol(symbol), _cid(client_id)
        return self._order_call('cancel', 'classic', cid, 'DELETE', '/fapi/v1/order',
                                [('symbol', symbol), ('origClientOrderId', cid)], parse=R.parse_order,
                                echo_field='client_order_id',
                                nf_types={-2011: NotFoundEvidenceType.CANCEL_UNKNOWN_ORDER,
                                          -2013: NotFoundEvidenceType.CANCEL_NO_SUCH_ORDER})

    def cancel_algo_order(self, client_algo_id):
        cid = _cid(client_algo_id, 'client_algo_id')
        return self._order_call('cancel_algo', 'algo', cid, 'DELETE', '/fapi/v1/algoOrder', [('clientAlgoId', cid)],
                                parse=R.parse_algo_order, echo_field='client_algo_id', allow_ack=True,
                                nf_types={-2011: NotFoundEvidenceType.ALGO_CANCEL_NOT_FOUND,
                                          -2013: NotFoundEvidenceType.ALGO_CANCEL_NOT_FOUND,
                                          'message': NotFoundEvidenceType.ALGO_CANCEL_NOT_FOUND})


def pairs_symbol(pairs):
    for k, v in pairs:
        if k == 'symbol':
            return v
    return None


def _code(data):
    """Binance 'code' as int when it is an int or an integer string (algo endpoints send "200"); else as given."""
    c = data.get('code')
    if isinstance(c, bool):
        return c
    if isinstance(c, str) and re.fullmatch(r'^-?[0-9]+$', c):
        return int(c)
    return c
