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

from newcore.account_context import (ACCOUNT_ORDER_LIMIT_PATH, ACCOUNT_SOURCE, EXCHANGE_INFO_PATH,
                                     MARKET_DATA_SOURCE, SOURCE_RETRIEVED_ON, LimitAuthority, RawLimitPayload)
from newcore.venue.binance_usdm_testnet import TESTNET_BASE_URL, HttpRequest, HttpResponse

from .ports import AccountFacts, PortError, SymbolFacts, SymbolFilters


API_KEY_ENV = "ZACKBOT_TNET02_API_KEY"
API_SECRET_ENV = "ZACKBOT_TNET02_API_SECRET"
_KEY = re.compile(r"[A-Za-z0-9]{16,128}")
_PARAM = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SYMBOL = re.compile(r"[A-Z0-9]{2,30}")
BALANCE_PATH = "/fapi/v2/balance"
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
                 recv_window_ms: int = 5000, timeout_s: float = 10.0):
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
        request = HttpRequest("GET", TESTNET_BASE_URL + path, payload + "&signature=" + signature,
                              (("X-MBX-APIKEY", api_key),), self._timeout_s)
        return self._decode(self._send(request))

    def public(self, path: str) -> tuple[object, bytes]:
        response = self._send(HttpRequest("GET", TESTNET_BASE_URL + path, "", (), self._timeout_s))
        return self._decode(response), response.body

    def _send(self, request: HttpRequest) -> HttpResponse:
        try:
            response = self._sender(request)
        except Exception:
            _fail("reader.transport")
        if not isinstance(response, HttpResponse) or type(response.body) is not bytes:
            _fail("reader.response")
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
class LiveAccountPort:
    """Fresh account facts from signed ``/fapi/v2/positionRisk`` (hedge mode, mark, leverage) and ``/fapi/v2/balance``."""

    def __init__(self, reader: SignedReader, clock: Callable[[], int], settlement_asset: str = "USDT"):
        self._reader, self._clock, self._asset = reader, clock, settlement_asset

    def facts(self, symbols: tuple[str, ...]) -> AccountFacts:
        if type(symbols) is not tuple or not symbols or any(type(s) is not str or _SYMBOL.fullmatch(s) is None
                                                            for s in symbols):
            _fail("account.symbols")
        rows = self._reader.signed(POSITION_RISK_PATH)
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
        balances = self._reader.signed(BALANCE_PATH)
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


__all__ = ["API_KEY_ENV", "API_SECRET_ENV", "EnvCredentialSource", "LiveAccountPort", "LiveError", "LiveFiltersPort",
           "SignedReader", "read_limit_payloads"]
