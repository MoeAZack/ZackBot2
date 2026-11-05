"""TNET-02 live testnet pieces: HMAC credential source, signed read client and the concrete account/filter ports.

Nothing here opens a socket.  Every request goes through an injected sender (the CLI passes NC-03B
``TestnetHttpSender``, which is bound to the exact testnet origin).  Credentials come only from the process
environment on the owner's machine; the secret is held privately, used only to sign, and never returned, printed,
pickled or placed in an error.  Errors carry stable codes only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping

from newcore.account_context import (ACCOUNT_ORDER_LIMIT_PATH, ACCOUNT_SOURCE, ENDPOINT_COSTS_V1, EXCHANGE_INFO_PATH,
                                     MARKET_DATA_SOURCE, SOURCE_RETRIEVED_ON, AdmissionRefused, Limiter,
                                     LimitAuthority, Operation, Pool, RawLimitPayload, Reservation)
from newcore.venue.binance_usdm_testnet import (TESTNET_BASE_URL, TESTNET_ORIGINS, HttpRequest, HttpResponse,
                                                RateEvidence)

from .ports import AccountFacts, PortError, SymbolFacts, SymbolFilters


API_KEY_ENV = "ZACKBOT_TNET02_API_KEY"
API_SECRET_ENV = "ZACKBOT_TNET02_API_SECRET"
_KEY = re.compile(r"[A-Za-z0-9]{16,128}")
_PARAM = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SYMBOL = re.compile(r"[A-Z0-9]{2,30}")
BALANCE_PATH = "/fapi/v2/balance"
SERVER_TIME_PATH = "/fapi/v1/time"
SIGNED_RECV_WINDOW_MS = 10_000          # the venue's maximum; absorbs network jitter on the real testnet
MAX_CLOCK_OFFSET_MS = 60_000
MAX_SYNC_RTT_MS = 5_000
SYNC_MARGIN_MS = 250                    # sign slightly behind server time: Binance rejects timestamps >1 s ahead
POSITION_RISK_PATH = "/fapi/v2/positionRisk"


class LiveError(PortError):
    """Stable code only; never venue text, URLs, query strings or credentials."""


def _fail(code: str) -> None:
    raise LiveError(code) from None


# --------------------------------------------------------------------------------------------- credentials
class EnvCredentialSource:
    """``CredentialSource`` over two environment variables set on the owner's PC for the dedicated testnet key.

    The secret is read once and kept only in this object.  ``repr``/``str`` are redacted and pickling is refused.
    """

    __slots__ = ("_key", "_secret")

    def __init__(self, environ: Mapping[str, str] | None = None):
        env = os.environ if environ is None else environ
        key, secret = env.get(API_KEY_ENV), env.get(API_SECRET_ENV)
        if type(key) is not str or _KEY.fullmatch(key) is None:
            _fail("credentials.api_key_missing")
        if type(secret) is not str or _KEY.fullmatch(secret) is None:
            _fail("credentials.api_secret_missing")
        if key == secret:
            _fail("credentials.key_equals_secret")
        self._key = key
        self._secret = secret.encode("ascii")

    def api_key(self) -> str:
        return self._key

    def sign(self, payload: bytes) -> str:
        if type(payload) is not bytes:
            _fail("credentials.payload")
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def __repr__(self) -> str:
        return "EnvCredentialSource(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        _fail("credentials.not_picklable")


# --------------------------------------------------------------------------------------------- reader
class SignedReader:
    """GET-only reads through the injected sender: signed account routes and the one unsigned public route."""

    def __init__(self, *, sender: Callable[[HttpRequest], HttpResponse], credentials, clock: Callable[[], int],
                 recv_window_ms: int = 5000, timeout_s: float = 10.0, base_url: str = TESTNET_BASE_URL):
        if type(base_url) is not str or base_url not in TESTNET_ORIGINS:
            _fail("reader.origin")
        self._origin = base_url
        self._observer: Callable[[RateEvidence], None] | None = None
        self._sender, self._credentials, self._clock = sender, credentials, clock
        self._recv_window_ms, self._timeout_s = recv_window_ms, timeout_s

    def signed(self, path: str, params: tuple[tuple[str, str], ...] = ()) -> object:
        for key, value in params:
            if _PARAM.fullmatch(key) is None or _PARAM.fullmatch(value) is None:
                _fail("reader.param")
        fields = list(params) + [("recvWindow", str(self._recv_window_ms)), ("timestamp", str(self._clock()))]
        payload = "&".join(f"{k}={v}" for k, v in fields)
        try:
            signature = self._credentials.sign(payload.encode("ascii"))
            api_key = self._credentials.api_key()
        except Exception:
            _fail("reader.credentials")
        request = HttpRequest("GET", self._origin + path, payload + "&signature=" + signature,
                              (("X-MBX-APIKEY", api_key),), self._timeout_s)
        return self._decode(self._send(request))

    def public(self, path: str) -> tuple[object, bytes]:
        response = self._send(HttpRequest("GET", self._origin + path, "", (), self._timeout_s))
        return self._decode(response), response.body

    @property
    def origin(self) -> str:
        return self._origin

    def set_rate_observer(self, observer: Callable[[RateEvidence], None]) -> None:
        """Feed every later response's rate evidence (status, Retry-After, used weight) to the NC-04 budget.

        Set once; a second binding is refused so evidence can never be split between two budgets."""
        if self._observer is not None or not callable(observer):
            _fail("reader.rate_observer")
        self._observer = observer

    def _send(self, request: HttpRequest) -> HttpResponse:
        if self._origin not in TESTNET_ORIGINS:
            _fail("reader.origin")
        try:
            response = self._sender(request)
        except Exception:
            _fail("reader.transport")
        if not isinstance(response, HttpResponse) or type(response.body) is not bytes:
            _fail("reader.response")
        if self._observer is not None:
            try:
                self._observer(RateEvidence(response.status, response.retry_after_ms, response.used_weight_1m,
                                            self._clock()))
            except Exception:
                _fail("reader.rate_observer")
        if response.status == 418:
            _fail("reader.http_418")
        if response.status == 429:
            _fail("reader.http_429")
        if response.status != 200:
            _fail("reader.http_status")
        return response

    @staticmethod
    def _decode(response: HttpResponse) -> object:
        try:
            return json.loads(response.body.decode("utf-8"), parse_float=Decimal, parse_int=int)
        except (UnicodeDecodeError, ValueError):
            _fail("reader.malformed")


# --------------------------------------------------------------------------------------------- server clock
def server_offset_ms(reader: SignedReader, local_clock: Callable[[], int], samples: int = 3) -> int:
    """Median offset (server - local) from unsigned ``/fapi/v1/time`` reads, each corrected by half its round trip.

    The real owner PC ran 1-2 s behind the testnet, and a signed read was refused with -1021 (outside recvWindow).
    A huge offset or a slow round trip is refused rather than trusted.
    """
    if type(samples) is not int or not 1 <= samples <= 9:
        _fail("clock.samples")
    offsets = []
    for _ in range(samples):
        before = local_clock()
        body, _raw = reader.public(SERVER_TIME_PATH)
        after = local_clock()
        server = body.get("serverTime") if type(body) is dict else None
        if type(server) is not int or server <= 0 or type(before) is not int or type(after) is not int:
            _fail("clock.server_time")
        if not 0 <= after - before <= MAX_SYNC_RTT_MS:
            _fail("clock.round_trip")
        offsets.append(server - (before + after) // 2)
    offsets.sort()
    offset = offsets[len(offsets) // 2]
    if abs(offset) > MAX_CLOCK_OFFSET_MS:
        _fail("clock.offset_too_large")
    return offset


class SyncedClock:
    """Wall clock in server time: ``local() + offset - margin``.  The offset is fixed for the run (measured once at
    start), so the clock never jumps mid-run."""

    __slots__ = ("_local", "_offset", "_margin")

    def __init__(self, local_clock: Callable[[], int], offset_ms: int, margin_ms: int = SYNC_MARGIN_MS):
        if not callable(local_clock) or type(offset_ms) is not int or abs(offset_ms) > MAX_CLOCK_OFFSET_MS:
            _fail("clock.synced")
        if type(margin_ms) is not int or not 0 <= margin_ms <= 1000:
            _fail("clock.synced")
        self._local, self._offset, self._margin = local_clock, offset_ms, margin_ms

    @property
    def offset_ms(self) -> int:
        return self._offset

    def __call__(self) -> int:
        return self._local() + self._offset - self._margin


def _decimal(value: object, code: str) -> Decimal:
    if type(value) is not str:
        _fail(code)
    try:
        out = Decimal(value)
    except InvalidOperation:
        _fail(code)
    if not out.is_finite():
        _fail(code)
    return out


# --------------------------------------------------------------------------------------------- account port
# Both account routes are documented at request weight 5 (positionRisk is in the NC-04 table; balance v2 carries the
# same documented weight), so each read reserves the POSITIONS cost from the GENERAL pool.  Account facts are never
# protection work: during any rate block they are refused rather than spent from the protection reserve.
_POSITIONS_COST = next(c for c in ENDPOINT_COSTS_V1 if c.operation is Operation.POSITIONS)
ACCOUNT_READ = Reservation(endpoint=_POSITIONS_COST, pool=Pool.GENERAL,
                           costs=tuple((limiter, int(_POSITIONS_COST.documented_costs[limiter] or 0))
                                       for limiter in Limiter))


class LiveAccountPort:
    """Fresh account facts from signed ``/fapi/v2/positionRisk`` (hedge mode, mark, leverage) and ``/fapi/v2/balance``.

    Once ``bind_budget`` is called (the wiring does it), each read first reserves weight from the account's NC-04
    budget, is refused while the budget is blocked, and reports its rate evidence back, so a 429/418 seen here blocks
    the venue too."""

    def __init__(self, reader: SignedReader, clock: Callable[[], int], settlement_asset: str = "USDT"):
        self._reader, self._clock, self._asset = reader, clock, settlement_asset
        self._budget = None

    @property
    def origin(self) -> str:
        return self._reader.origin

    def bind_budget(self, budget) -> None:
        if self._budget is not None or not callable(getattr(budget, "reserve", None)) or not callable(
                getattr(budget, "observe", None)):
            _fail("account.budget_binding")
        self._reader.set_rate_observer(budget.observe)
        self._budget = budget

    def _signed(self, path: str) -> object:
        if self._budget is not None:
            try:
                self._budget.reserve(ACCOUNT_READ)
            except AdmissionRefused:
                _fail("account.budget_refused")
        return self._reader.signed(path)

    def facts(self, symbols: tuple[str, ...]) -> AccountFacts:
        if type(symbols) is not tuple or not symbols or any(type(s) is not str or _SYMBOL.fullmatch(s) is None
                                                            for s in symbols):
            _fail("account.symbols")
        rows = self._signed(POSITION_RISK_PATH)
        if type(rows) is not list or not rows or any(type(r) is not dict for r in rows):
            _fail("account.position_rows")
        sides = {r.get("positionSide") for r in rows}
        if not sides <= {"LONG", "SHORT", "BOTH"}:
            _fail("account.position_side")
        hedge = "BOTH" not in sides
        out = []
        for symbol in symbols:
            mine = [r for r in rows if r.get("symbol") == symbol]
            if not mine:
                _fail("account.symbol_missing")
            marks = {_decimal(r.get("markPrice"), "account.mark_price") for r in mine}
            try:
                leverages = {int(r.get("leverage")) for r in mine}
            except (TypeError, ValueError):
                _fail("account.leverage")
            if len(marks) != 1:
                _fail("account.mark_price")
            # conservative: the highest leverage set on either side is the one the case ceiling is checked against
            out.append(SymbolFacts(symbol, marks.pop(), max(leverages)))
        balances = self._signed(BALANCE_PATH)
        if type(balances) is not list or any(type(b) is not dict for b in balances):
            _fail("account.balance_rows")
        asset = [b for b in balances if b.get("asset") == self._asset]
        if len(asset) != 1:
            _fail("account.settlement_asset")
        row = asset[0]
        equity = _decimal(row.get("balance"), "account.balance") + _decimal(row.get("crossUnPnl"),
                                                                             "account.unrealized")
        available = _decimal(row.get("availableBalance"), "account.available")
        try:
            return AccountFacts(self._clock(), hedge, self._asset, equity, max(available, Decimal(0)), tuple(out))
        except PortError:
            _fail("account.facts_invalid")


# --------------------------------------------------------------------------------------------- filters port
class LiveFiltersPort:
    """Symbol filters from one public ``exchangeInfo`` read, pinned by the SHA-256 of its exact bytes."""

    def __init__(self, exchange_info: object, raw: bytes):
        if type(exchange_info) is not dict or type(exchange_info.get("symbols")) is not list:
            _fail("filters.exchange_info")
        self._symbols = {s.get("symbol"): s for s in exchange_info["symbols"] if type(s) is dict}
        self._sha = hashlib.sha256(raw).hexdigest()

    def filters(self, symbol: str) -> SymbolFilters:
        spec = self._symbols.get(symbol)
        if spec is None:
            _fail("filters.symbol_missing")
        if (spec.get("status"), spec.get("contractType"), spec.get("quoteAsset"), spec.get("marginAsset")) != (
                "TRADING", "PERPETUAL", "USDT", "USDT"):
            _fail("filters.symbol_not_tradable")
        found = {f.get("filterType"): f for f in spec.get("filters", ()) if type(f) is dict}
        try:
            price, lot, market = found["PRICE_FILTER"], found["LOT_SIZE"], found["MARKET_LOT_SIZE"]
            notional = found["MIN_NOTIONAL"]
        except KeyError:
            _fail("filters.missing")
        step = max(_decimal(lot.get("stepSize"), "filters.step"), _decimal(market.get("stepSize"), "filters.step"))
        min_qty = max(_decimal(lot.get("minQty"), "filters.min_qty"),
                      _decimal(market.get("minQty"), "filters.min_qty"))
        max_qty = min(_decimal(lot.get("maxQty"), "filters.max_qty"),
                      _decimal(market.get("maxQty"), "filters.max_qty"))
        tick = _decimal(price.get("tickSize"), "filters.tick")
        min_notional = _decimal(notional.get("notional"), "filters.notional")
        try:
            return SymbolFilters(symbol, tick, step, min_qty, max_qty, min_notional, self._sha)
        except PortError:
            _fail("filters.invalid")


# --------------------------------------------------------------------------------------------- limit payloads
def read_limit_payloads(reader: SignedReader) -> tuple[RawLimitPayload, RawLimitPayload, object, bytes]:
    """Read the two authoritative NC-04 limit payloads; also returns exchangeInfo for the filters port."""
    info, raw = reader.public(EXCHANGE_INFO_PATH)
    if type(info) is not dict:
        _fail("limits.exchange_info")
    orders = reader.signed(ACCOUNT_ORDER_LIMIT_PATH)
    try:
        exchange = RawLimitPayload(authority=LimitAuthority.EXCHANGE_INFO, method="GET", path=EXCHANGE_INFO_PATH,
                                   source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
                                   payload={"rateLimits": info.get("rateLimits")})
        account = RawLimitPayload(authority=LimitAuthority.USER_RATE_LIMIT, method="GET",
                                  path=ACCOUNT_ORDER_LIMIT_PATH, source_url=ACCOUNT_SOURCE,
                                  retrieved_on=SOURCE_RETRIEVED_ON, payload=orders)
    except Exception:
        _fail("limits.payload")
    return exchange, account, info, raw


# --------------------------------------------------------------------------------------------- venue factory
def make_testnet_venue(*, origin: str, sender: Callable[[HttpRequest], HttpResponse], clock: Callable[[], int],
                       environ: Mapping[str, str] | None = None, rate_observer=None):
    """One-call ``BinanceUsdMTestnetVenue`` for launchers outside ``newcore`` (e.g. the RT runtime's ``tools/`` CLI).

    ``origin`` must be one exact approved testnet origin (take it from the confirmed account file); there is no
    default and no fallback.  Credentials come only from ``ZACKBOT_TNET02_API_KEY`` / ``_SECRET`` in ``environ``
    (default: the process environment).  ``sender`` is the caller's ``TestnetHttpSender(origin)`` (this package never
    imports the socket module); a sender that declares a different origin, or none, is refused.  Nothing is sent here.
    Returns ``(venue, key_digest)`` so the caller can check the digest against its confirmed binding before use.
    """
    from newcore.venue.binance_usdm_testnet import BinanceUsdMTestnetVenue

    from .wiring import key_digest

    if type(origin) is not str or origin not in TESTNET_ORIGINS:
        _fail("venue.origin")
    credentials = EnvCredentialSource(environ)
    if not callable(sender) or getattr(sender, "origin", None) != origin:
        _fail("venue.origin_mismatch")
    venue = BinanceUsdMTestnetVenue(environment="testnet", base_url=origin, sender=sender, credentials=credentials,
                                    clock=clock, rate_observer=rate_observer)
    return venue, key_digest(credentials.api_key())


__all__ = ["API_KEY_ENV", "API_SECRET_ENV", "EnvCredentialSource", "LiveAccountPort", "LiveError", "LiveFiltersPort",
           "SIGNED_RECV_WINDOW_MS", "SignedReader", "SyncedClock", "make_testnet_venue", "read_limit_payloads",
           "server_offset_ms"]
