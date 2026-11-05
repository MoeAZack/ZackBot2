"""Hostile-capable in-memory Binance USD-M testnet HTTP simulator for TNET-02 tests.

It answers the exact request shapes the #291 ``BinanceUsdMTestnetVenue`` sends, records every wire request, and
exposes knobs that tests use to inject hostile behaviour (foreign orders, partial algo reads, rejected stops,
non-hedge accounts, missing answers, price paths).  It never touches the network.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable
from urllib.parse import parse_qsl, urlsplit

from newcore.account_context import (ACCOUNT_ORDER_LIMIT_PATH, ACCOUNT_SOURCE, EXCHANGE_INFO_PATH,
                                     MARKET_DATA_SOURCE, SOURCE_RETRIEVED_ON, LimitAuthority, RawLimitPayload)
from newcore.venue.binance_usdm_testnet import TESTNET_BASE_URL, HttpRequest, HttpResponse

from newcore.tnet02.ports import AccountFacts, SymbolFacts, SymbolFilters


SECRET_KEY = "TNET02-SECRET-API-KEY-MARKER-7f1d"
SECRET_SIG = "TNET02-SECRET-SIGNATURE-MARKER-c09e"


class FakeClock:
    def __init__(self, start: int = 2_000_000_000_000):
        self.now = start

    def __call__(self) -> int:
        return self.now


class FakeCredentials:
    def api_key(self) -> str:
        return SECRET_KEY

    def sign(self, payload: bytes) -> str:
        return SECRET_SIG


def ok(body) -> HttpResponse:
    return HttpResponse(status=200, body=json.dumps(body).encode("utf-8"))


def err(code: int, status: int = 400) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps({"code": code, "msg": "fake"}).encode("utf-8"))


@dataclass
class Order:
    order_id: int
    client_id: str
    symbol: str
    side: str            # BUY/SELL
    position_side: str   # LONG/SHORT
    qty: Decimal
    reduce: bool
    status: str = "NEW"
    executed: Decimal = Decimal("0")
    avg: Decimal = Decimal("0")


@dataclass
class Algo:
    algo_id: int
    client_id: str
    symbol: str
    position_side: str
    qty: Decimal
    trigger: Decimal
    status: str = "NEW"
    child: int | None = None


@dataclass
class Trade:
    trade_id: int
    order_id: int
    symbol: str
    position_side: str
    qty: Decimal
    price: Decimal
    time: int


@dataclass
class FakeBinance:
    clock: FakeClock
    marks: dict = field(default_factory=lambda: {"BTCUSDT": Decimal("60000"), "ETHUSDT": Decimal("3000")})
    leverage: dict = field(default_factory=lambda: {"BTCUSDT": 3, "ETHUSDT": 3})
    hedge_mode: bool = True
    equity: Decimal = Decimal("5000")
    positions: dict = field(default_factory=dict)            # (symbol, side) -> qty
    orders: dict = field(default_factory=dict)               # client_id -> Order
    algos: dict = field(default_factory=dict)                # client_id -> Algo
    trades: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    price_path: Callable[[int, str], Decimal] | None = None
    market_ack_only: bool = True
    fail_algo_open_read: bool = False
    reject_algo_stop: bool = False
    hidden_answers: set = field(default_factory=set)
    next_id: int = 1000

    # ----------------------------------------------------------------------------------------- simulation
    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def tick(self) -> None:
        if self.price_path is not None:
            for symbol in list(self.marks):
                self.marks[symbol] = self.price_path(self.clock.now, symbol)
        for algo in list(self.algos.values()):
            if algo.status not in ("NEW", "WORKING"):
                continue
            mark = self.marks[algo.symbol]
            hit = mark <= algo.trigger if algo.position_side == "LONG" else mark >= algo.trigger
            if not hit:
                continue
            held = self.positions.get((algo.symbol, algo.position_side), Decimal("0"))
            qty = min(held, algo.qty)
            child = self._id()
            algo.status, algo.child = "FINISHED", child
            if qty > 0:
                self._fill(child, algo.symbol, algo.position_side, -qty, mark)

    def _fill(self, order_id: int, symbol: str, side: str, delta: Decimal, price: Decimal) -> None:
        key = (symbol, side)
        new = self.positions.get(key, Decimal("0")) + delta
        if new < 0:
            raise AssertionError("fake position went negative")
        self.positions[key] = new
        self.trades.append(Trade(self._id(), order_id, symbol, side, abs(delta), price, self.clock.now))

    def advance(self, ms: int) -> None:
        self.clock.now += ms
        self.tick()

    # ----------------------------------------------------------------------------------------- HTTP
    def __call__(self, request: HttpRequest) -> HttpResponse:
        assert isinstance(request, HttpRequest)
        parts = urlsplit(request.url)
        assert f"{parts.scheme}://{parts.netloc}" == getattr(self, "origin", TESTNET_BASE_URL)
        params = dict(parse_qsl(request.query, keep_blank_values=True))
        self.requests.append((request.method, parts.path, params))
        self.tick()
        handler = getattr(self, "_" + request.method.lower() + parts.path.replace("/", "_"), None)
        if handler is None:
            return err(-1000, 404)
        return handler(params)

    def _post_fapi_v1_order(self, p):
        if p["type"] == "STOP_MARKET":
            return err(-4120)
        cid = p["newClientOrderId"]
        if cid in self.orders:
            return err(-4116)
        symbol, ps, qty = p["symbol"], p["positionSide"], Decimal(p["quantity"])
        reduce = (ps == "LONG" and p["side"] == "SELL") or (ps == "SHORT" and p["side"] == "BUY")
        held = self.positions.get((symbol, ps), Decimal("0"))
        if reduce and qty > held:
            return err(-2022)
        order = Order(self._id(), cid, symbol, p["side"], ps, qty, reduce)
        self.orders[cid] = order
        mark = self.marks[symbol]
        order.status, order.executed, order.avg = "FILLED", qty, mark
        self._fill(order.order_id, symbol, ps, -qty if reduce else qty, mark)
        if self.market_ack_only:
            return ok({"symbol": symbol, "clientOrderId": cid, "orderId": order.order_id, "status": "NEW",
                       "executedQty": "0", "avgPrice": "0"})
        return self._order_body(order)

    def _order_body(self, order: Order) -> HttpResponse:
        return ok({"symbol": order.symbol, "clientOrderId": order.client_id, "orderId": order.order_id,
                   "status": order.status, "executedQty": format(order.executed, "f"),
                   "avgPrice": format(order.avg, "f")})

    def _get_fapi_v1_order(self, p):
        order = self.orders.get(p["origClientOrderId"])
        if order is None or order.symbol != p["symbol"] or order.client_id in self.hidden_answers:
            return err(-2013)
        return self._order_body(order)

    def _delete_fapi_v1_order(self, p):
        order = self.orders.get(p["origClientOrderId"])
        if order is None or order.status != "NEW":
            return err(-2011)
        order.status = "CANCELED"
        return self._order_body(order)

    def _algo_body(self, algo: Algo) -> HttpResponse:
        body = {"algoId": algo.algo_id, "clientAlgoId": algo.client_id, "symbol": algo.symbol,
                "algoStatus": algo.status}
        if algo.child is not None:
            body["actualOrderId"] = algo.child
        return ok(body)

    def _post_fapi_v1_algoOrder(self, p):
        if self.reject_algo_stop:
            return err(-2021)
        cid = p["clientAlgoId"]
        if cid in self.algos:
            return err(-4116)
        algo = Algo(self._id(), cid, p["symbol"], p["positionSide"], Decimal(p["quantity"]),
                    Decimal(p["triggerPrice"]))
        self.algos[cid] = algo
        return self._algo_body(algo)

    def _get_fapi_v1_algoOrder(self, p):
        algo = self.algos.get(p["clientAlgoId"])
        if algo is None:
            return err(-2013)
        return self._algo_body(algo)

    def _delete_fapi_v1_algoOrder(self, p):
        algo = self.algos.get(p["clientAlgoId"])
        if algo is None or algo.status not in ("NEW", "WORKING"):
            return err(-2011)
        algo.status = "CANCELED"
        return self._algo_body(algo)

    def _get_fapi_v1_openOrders(self, p):
        rows = [o for o in self.orders.values() if o.status == "NEW"
                and ("symbol" not in p or o.symbol == p["symbol"])]
        return ok([{"symbol": o.symbol, "positionSide": o.position_side, "clientOrderId": o.client_id,
                    "orderId": o.order_id, "origQty": format(o.qty, "f"), "executedQty": "0",
                    "reduceOnly": o.reduce, "closePosition": False, "stopPrice": "0", "type": "LIMIT",
                    "status": "NEW"} for o in rows])

    def _get_fapi_v1_openAlgoOrders(self, p):
        if self.fail_algo_open_read:
            return HttpResponse(status=503, body=b"{}")
        rows = [a for a in self.algos.values() if a.status in ("NEW", "WORKING")
                and ("symbol" not in p or a.symbol == p["symbol"])]
        return ok([{"symbol": a.symbol, "positionSide": a.position_side, "clientAlgoId": a.client_id,
                    "algoId": a.algo_id, "quantity": format(a.qty, "f"), "triggerPrice": format(a.trigger, "f"),
                    "orderType": "STOP_MARKET", "algoStatus": a.status, "reduceOnly": True} for a in rows])

    def _get_fapi_v2_positionRisk(self, p):
        if not self.hedge_mode:
            return ok([{"symbol": s, "positionSide": "BOTH", "positionAmt": "0", "entryPrice": "0",
                        "markPrice": format(self.marks[s], "f"), "leverage": str(self.leverage[s])}
                       for s in self.marks if "symbol" not in p or s == p["symbol"]])
        rows = []
        for symbol in self.marks:
            if "symbol" in p and symbol != p["symbol"]:
                continue
            for side in ("LONG", "SHORT"):
                qty = self.positions.get((symbol, side), Decimal("0"))
                amt = qty if side == "LONG" else -qty
                rows.append({"symbol": symbol, "positionSide": side, "positionAmt": format(amt, "f"),
                             "entryPrice": format(self.marks[symbol] if qty else Decimal("0"), "f"),
                             "markPrice": format(self.marks[symbol], "f"),
                             "leverage": str(self.leverage[symbol])})
        return ok(rows)

    def _get_fapi_v2_balance(self, p):
        return ok([{"asset": "BNB", "balance": "0", "crossUnPnl": "0", "availableBalance": "0"},
                   {"asset": "USDT", "balance": format(self.equity, "f"), "crossUnPnl": "0",
                    "availableBalance": format(self.equity, "f")}])

    def _get_fapi_v1_exchangeInfo(self, p):
        assert p == {}
        symbols = []
        for symbol, tick, notional in (("BTCUSDT", "0.10", "100"), ("ETHUSDT", "0.01", "20")):
            symbols.append({"symbol": symbol, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT",
                            "marginAsset": "USDT", "filters": [
                                {"filterType": "PRICE_FILTER", "tickSize": tick},
                                {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "1000"},
                                {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001",
                                 "maxQty": "120"},
                                {"filterType": "MIN_NOTIONAL", "notional": notional}]})
        return ok({"rateLimits": [{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1,
                                   "limit": 2400}], "symbols": symbols})

    def _get_fapi_v1_rateLimit_order(self, p):
        return ok([{"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": 300},
                   {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": 1200}])

    def _get_fapi_v1_userTrades(self, p):
        rows = [t for t in self.trades if t.symbol == p["symbol"] and str(t.order_id) == p["orderId"]]
        return ok([{"symbol": t.symbol, "orderId": t.order_id, "id": t.trade_id, "positionSide": t.position_side,
                    "maker": False, "commissionAsset": "USDT", "qty": format(t.qty, "f"),
                    "price": format(t.price, "f"), "commission": "0.01", "realizedPnl": "0", "time": t.time}
                   for t in rows])

    # ----------------------------------------------------------------------------------------- ports
    def order_requests(self) -> list:
        return [r for r in self.requests if r[0] == "POST"]


class FakeAccountPort:
    def __init__(self, fake: FakeBinance):
        self.fake = fake

    def facts(self, symbols):
        f = self.fake
        f.tick()
        return AccountFacts(f.clock.now, f.hedge_mode, "USDT", f.equity, f.equity,
                            tuple(SymbolFacts(s, f.marks[s], f.leverage[s]) for s in symbols))


FILTER_SHA = hashlib.sha256(b"fake-filters").hexdigest()


class FakeFiltersPort:
    def filters(self, symbol):
        if symbol == "BTCUSDT":
            return SymbolFilters(symbol, Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("1000"),
                                 Decimal("100"), FILTER_SHA)
        return SymbolFilters(symbol, Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("10000"),
                             Decimal("20"), FILTER_SHA)


def limit_payloads(req: int = 2400, o10: int = 300, o1m: int = 1200):
    exchange = RawLimitPayload(
        authority=LimitAuthority.EXCHANGE_INFO, method="GET", path=EXCHANGE_INFO_PATH,
        source_url=MARKET_DATA_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload={"rateLimits": [{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1,
                                 "limit": req}]})
    account = RawLimitPayload(
        authority=LimitAuthority.USER_RATE_LIMIT, method="GET", path=ACCOUNT_ORDER_LIMIT_PATH,
        source_url=ACCOUNT_SOURCE, retrieved_on=SOURCE_RETRIEVED_ON,
        payload=[{"rateLimitType": "ORDERS", "interval": "SECOND", "intervalNum": 10, "limit": o10},
                 {"rateLimitType": "ORDERS", "interval": "MINUTE", "intervalNum": 1, "limit": o1m}])
    return exchange, account
