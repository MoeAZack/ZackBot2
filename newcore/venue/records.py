"""Strict parsers for Binance USD-M Futures answers.

Numbers are Decimal, never float: JSON is decoded with parse_float=Decimal, NaN/Infinity are refused, duplicate object
keys are refused, and numeric strings must match `-?digits(.digits)?`. Booleans must be JSON booleans. A malformed
answer raises MalformedResponse; the transport turns that into UNKNOWN (an unreadable answer is never "empty" and
never "no position").
"""
import json
import re
from dataclasses import dataclass
from decimal import Decimal


class MalformedResponse(ValueError):
    """The answer did not have the documented shape."""


# ---------- JSON + scalar validators ----------

def _no_dup_pairs(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise MalformedResponse('duplicate JSON key')
        out[k] = v
    return out


def _bad_constant(name):
    raise MalformedResponse(f'non-finite JSON constant {name}')


def decode_json(body):
    if not isinstance(body, (bytes, bytearray)):
        raise MalformedResponse('body is not bytes')
    try:
        text = bytes(body).decode('utf-8')
        return json.loads(text, parse_float=Decimal, parse_constant=_bad_constant, object_pairs_hook=_no_dup_pairs)
    except MalformedResponse:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError, ArithmeticError) as ex:     # 1E+99999999999999999999
        raise MalformedResponse(f'body is not JSON ({type(ex).__name__})') from None


_NUM = re.compile(r'^-?[0-9]+(\.[0-9]+)?$')
MAX_NUM_TEXT = 64                  # a venue number longer than this is malformed (no 1 MB digit strings)
MAX_ADJUSTED = 40                  # |exponent| cap: 1e999999999 is refused before any arithmetic (no OOM)
MAX_INT = 2 ** 63
MS_MIN, MS_MAX = 946_684_800_000, 4_102_444_799_999      # 2000-01-01 .. 2099-12-31 UTC


def dec(row, key, *, nonneg=False, positive=False, optional=False):
    if not isinstance(row, dict):
        raise MalformedResponse('row is not an object')
    if key not in row or row[key] is None or row[key] == '':
        if optional:
            return None
        raise MalformedResponse(f'{key}: missing number')
    v = row[key]
    if isinstance(v, bool):
        raise MalformedResponse(f'{key}: boolean is not a number')
    if isinstance(v, int):
        d = Decimal(v)
    elif isinstance(v, Decimal):
        if not v.is_finite():
            raise MalformedResponse(f'{key}: non-finite')
        d = v
    elif isinstance(v, str) and len(v) <= MAX_NUM_TEXT and _NUM.fullmatch(v):
        d = Decimal(v)
    else:
        raise MalformedResponse(f'{key}: not a decimal number')
    if d and not -MAX_ADJUSTED <= d.adjusted() <= MAX_ADJUSTED or len(d.as_tuple().digits) > MAX_NUM_TEXT:
        raise MalformedResponse(f'{key}: out of range')
    if positive and d <= 0:
        raise MalformedResponse(f'{key}: must be > 0')
    if nonneg and d < 0:
        raise MalformedResponse(f'{key}: must be >= 0')
    return d


def server_ms(row, key):
    """A venue timestamp in UTC ms, 2000..2099 (1e20 / 1e300 / 2.5e14 are malformed, never OSError later)."""
    v = integer(row, key, positive=True)
    if not MS_MIN <= v <= MS_MAX:
        raise MalformedResponse(f'{key}: not a UTC ms timestamp in 2000..2099')
    return v


def integer(row, key, *, optional=False, positive=False, nonneg=False):
    if not isinstance(row, dict):
        raise MalformedResponse('row is not an object')
    if key not in row or row[key] is None or row[key] == '':
        if optional:
            return None
        raise MalformedResponse(f'{key}: missing integer')
    v = row[key]
    if isinstance(v, bool):
        raise MalformedResponse(f'{key}: boolean is not an integer')
    if isinstance(v, str) and len(v) <= MAX_NUM_TEXT and re.fullmatch(r'^-?[0-9]+$', v):
        v = int(v)
    if not isinstance(v, int):
        raise MalformedResponse(f'{key}: not an integer')
    if not -MAX_INT < v < MAX_INT:
        raise MalformedResponse(f'{key}: out of range')
    if positive and v <= 0:
        raise MalformedResponse(f'{key}: must be > 0')
    if nonneg and v < 0:
        raise MalformedResponse(f'{key}: must be >= 0')
    return v


def text(row, key, *, optional=False, allowed=None):
    if not isinstance(row, dict):
        raise MalformedResponse('row is not an object')
    v = row.get(key)
    if v is None and optional:
        return None
    if not isinstance(v, str) or not v:
        raise MalformedResponse(f'{key}: missing text')
    if allowed is not None and v not in allowed:
        raise MalformedResponse(f'{key}: unexpected value')
    return v


def flag(row, key, *, optional=False):
    if not isinstance(row, dict):
        raise MalformedResponse('row is not an object')
    if key not in row:
        if optional:
            return None
        raise MalformedResponse(f'{key}: missing boolean')
    v = row[key]
    if not isinstance(v, bool):
        raise MalformedResponse(f'{key}: not a boolean')
    return v


def as_list(value, what):
    if not isinstance(value, list):
        raise MalformedResponse(f'{what}: expected a list')
    return value


def as_obj(value, what):
    if not isinstance(value, dict):
        raise MalformedResponse(f'{what}: expected an object')
    return value


SIDES = ('BUY', 'SELL')
POSITION_SIDES = ('LONG', 'SHORT', 'BOTH')


# ---------- exchangeInfo ----------

@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    status: str
    contract_type: str
    quote_asset: str
    margin_asset: str
    price_precision: int
    quantity_precision: int
    tick_size: Decimal
    min_price: Decimal
    max_price: Decimal
    step_size: Decimal
    min_qty: Decimal
    max_qty: Decimal
    market_step_size: Decimal
    market_min_qty: Decimal
    market_max_qty: Decimal
    min_notional: Decimal
    max_num_orders: object          # int or None
    max_num_algo_orders: object     # int or None
    order_types: tuple


@dataclass(frozen=True)
class ExchangeInfo:
    server_time_ms: int
    rate_limits: tuple              # ((rateLimitType, interval, intervalNum, limit), ...)
    symbols: dict                   # symbol -> SymbolRules
    unparseable: tuple              # symbols whose rules did not parse (absent from .symbols: unknown, never guessed)


def parse_symbol_rules(row):
    filters = {}
    for f in as_list(row.get('filters'), 'filters'):
        ft = text(f, 'filterType')
        if ft in filters:
            raise MalformedResponse('duplicate filter')
        filters[ft] = f
    for need in ('PRICE_FILTER', 'LOT_SIZE', 'MARKET_LOT_SIZE', 'MIN_NOTIONAL'):
        if need not in filters:
            raise MalformedResponse(f'missing {need}')
    pf, ls, ml, mn = filters['PRICE_FILTER'], filters['LOT_SIZE'], filters['MARKET_LOT_SIZE'], filters['MIN_NOTIONAL']
    mno, mna = filters.get('MAX_NUM_ORDERS'), filters.get('MAX_NUM_ALGO_ORDERS')
    types = tuple(as_list(row.get('orderTypes', []), 'orderTypes'))
    if not all(isinstance(t, str) for t in types):
        raise MalformedResponse('orderTypes: not text')
    return SymbolRules(
        symbol=text(row, 'symbol'), status=text(row, 'status'), contract_type=text(row, 'contractType'),
        quote_asset=text(row, 'quoteAsset'), margin_asset=text(row, 'marginAsset'),
        price_precision=integer(row, 'pricePrecision', nonneg=True),
        quantity_precision=integer(row, 'quantityPrecision', nonneg=True),
        tick_size=dec(pf, 'tickSize', positive=True), min_price=dec(pf, 'minPrice', nonneg=True),
        max_price=dec(pf, 'maxPrice', positive=True),
        step_size=dec(ls, 'stepSize', positive=True), min_qty=dec(ls, 'minQty', positive=True),
        max_qty=dec(ls, 'maxQty', positive=True),
        market_step_size=dec(ml, 'stepSize', positive=True), market_min_qty=dec(ml, 'minQty', positive=True),
        market_max_qty=dec(ml, 'maxQty', positive=True),
        min_notional=dec(mn, 'notional', positive=True),
        max_num_orders=integer(mno, 'limit', positive=True) if mno is not None else None,
        max_num_algo_orders=integer(mna, 'limit', positive=True) if mna is not None else None,
        order_types=types)


def parse_exchange_info(data):
    data = as_obj(data, 'exchangeInfo')
    limits = []
    for rl in as_list(data.get('rateLimits', []), 'rateLimits'):
        limits.append((text(rl, 'rateLimitType'), text(rl, 'interval'), integer(rl, 'intervalNum', positive=True),
                       integer(rl, 'limit', positive=True)))
    symbols, bad = {}, []
    for row in as_list(data.get('symbols'), 'symbols'):
        name = row.get('symbol') if isinstance(row, dict) else None
        try:
            rules = parse_symbol_rules(as_obj(row, 'symbol'))
        except MalformedResponse:
            bad.append(name if isinstance(name, str) else '?')
            continue
        if rules.symbol in symbols:
            raise MalformedResponse('duplicate symbol in exchangeInfo')
        symbols[rules.symbol] = rules
    return ExchangeInfo(server_time_ms=server_ms(data, 'serverTime'), rate_limits=tuple(limits),
                        symbols=symbols, unparseable=tuple(bad))


# ---------- klines ----------

@dataclass(frozen=True)
class Kline:
    open_time_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    close_time_ms: int
    quote_volume: Decimal
    trades: int
    taker_buy_base: Decimal
    taker_buy_quote: Decimal

    def is_closed_at(self, now_ms):
        """A candle is closed once now is past its close time. The forming candle must never feed a signal."""
        return self.close_time_ms < now_ms


def parse_klines(data):
    out, last_open = [], None
    for raw in as_list(data, 'klines'):
        if not isinstance(raw, list) or len(raw) < 11:
            raise MalformedResponse('kline row: expected an array of at least 11 items')
        row = dict(enumerate(raw))
        k = Kline(open_time_ms=integer(row, 0, nonneg=True), open=dec(row, 1, positive=True),
                  high=dec(row, 2, positive=True), low=dec(row, 3, positive=True), close=dec(row, 4, positive=True),
                  volume=dec(row, 5, nonneg=True), close_time_ms=integer(row, 6, positive=True),
                  quote_volume=dec(row, 7, nonneg=True), trades=integer(row, 8, nonneg=True),
                  taker_buy_base=dec(row, 9, nonneg=True), taker_buy_quote=dec(row, 10, nonneg=True))
        if not (k.low <= min(k.open, k.close) and max(k.open, k.close) <= k.high):
            raise MalformedResponse('kline: OHLC out of order')
        if k.close_time_ms <= k.open_time_ms:
            raise MalformedResponse('kline: close time not after open time')
        if last_open is not None and k.open_time_ms <= last_open:
            raise MalformedResponse('klines: not strictly ascending')
        last_open = k.open_time_ms
        out.append(k)
    return tuple(out)


# ---------- account / positions ----------

@dataclass(frozen=True)
class AssetBalance:
    asset: str
    wallet_balance: Decimal
    margin_balance: Decimal
    unrealized_profit: Decimal
    available_balance: Decimal


@dataclass(frozen=True)
class AccountSnapshot:
    can_trade: bool
    total_wallet_balance: Decimal
    total_margin_balance: Decimal
    total_unrealized_profit: Decimal
    available_balance: Decimal
    update_time_ms: object          # int or None
    assets: tuple                   # AssetBalance, ...


def parse_account(data):
    data = as_obj(data, 'account')
    assets = tuple(AssetBalance(asset=text(a, 'asset'), wallet_balance=dec(a, 'walletBalance'),
                                margin_balance=dec(a, 'marginBalance'), unrealized_profit=dec(a, 'unrealizedProfit'),
                                available_balance=dec(a, 'availableBalance'))
                   for a in as_list(data.get('assets'), 'assets'))
    return AccountSnapshot(can_trade=flag(data, 'canTrade'), total_wallet_balance=dec(data, 'totalWalletBalance'),
                           total_margin_balance=dec(data, 'totalMarginBalance'),
                           total_unrealized_profit=dec(data, 'totalUnrealizedProfit'),
                           available_balance=dec(data, 'availableBalance'),
                           update_time_ms=integer(data, 'updateTime', optional=True, nonneg=True), assets=assets)


@dataclass(frozen=True)
class PositionRow:
    """One positionRisk row, raw. position_amt keeps Binance's sign; side is taken from positionSide, never from the
    sign (NC-01). A 'BOTH' row is a one-way-mode row: its direction is only knowable from the sign, so the adapter
    must refuse to treat it as hedge-mode data."""
    symbol: str
    position_side: str
    position_amt: Decimal
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal
    liquidation_price: object       # Decimal or None
    notional: object                # Decimal or None
    leverage: int
    margin_type: str
    update_time_ms: int


def parse_positions(data):
    out, seen = [], set()
    for p in as_list(data, 'positionRisk'):
        row = PositionRow(symbol=text(p, 'symbol'), position_side=text(p, 'positionSide', allowed=POSITION_SIDES),
                          position_amt=dec(p, 'positionAmt'), entry_price=dec(p, 'entryPrice', nonneg=True),
                          mark_price=dec(p, 'markPrice', nonneg=True), unrealized_pnl=dec(p, 'unRealizedProfit'),
                          liquidation_price=dec(p, 'liquidationPrice', nonneg=True, optional=True),
                          notional=dec(p, 'notional', optional=True), leverage=integer(p, 'leverage', positive=True),
                          margin_type=text(p, 'marginType', allowed=('cross', 'isolated')),
                          update_time_ms=integer(p, 'updateTime', nonneg=True))
        if row.position_side == 'LONG' and row.position_amt < 0:
            raise MalformedResponse('LONG row with a negative amount')
        if row.position_side == 'SHORT' and row.position_amt > 0:
            raise MalformedResponse('SHORT row with a positive amount')
        key = (row.symbol, row.position_side)
        if key in seen:
            raise MalformedResponse('duplicate position row')
        seen.add(key)
        out.append(row)
    return tuple(out)


def parse_dual_side(data):
    return flag(as_obj(data, 'positionSide/dual'), 'dualSidePosition')


# ---------- orders ----------

ORDER_STATUSES = ('NEW', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'EXPIRED_IN_MATCH')
FINAL_ORDER_STATUSES = ('FILLED', 'CANCELED', 'REJECTED', 'EXPIRED', 'EXPIRED_IN_MATCH')


@dataclass(frozen=True)
class OrderRecord:
    order_id: int
    client_order_id: str
    symbol: str
    status: str
    type: str
    orig_type: object
    side: str
    position_side: str
    orig_qty: Decimal
    executed_qty: Decimal
    avg_price: object               # Decimal or None (0 / missing before any fill)
    cum_quote: object               # Decimal or None
    price: object
    stop_price: object
    reduce_only: bool
    close_position: bool
    working_type: object
    update_time_ms: int

    @property
    def is_final(self):
        return self.status in FINAL_ORDER_STATUSES


def parse_order(o):
    o = as_obj(o, 'order')
    rec = OrderRecord(order_id=integer(o, 'orderId', positive=True), client_order_id=text(o, 'clientOrderId'),
                      symbol=text(o, 'symbol'), status=text(o, 'status', allowed=ORDER_STATUSES),
                      type=text(o, 'type'), orig_type=text(o, 'origType', optional=True),
                      side=text(o, 'side', allowed=SIDES),
                      position_side=text(o, 'positionSide', allowed=POSITION_SIDES),
                      orig_qty=dec(o, 'origQty', nonneg=True), executed_qty=dec(o, 'executedQty', nonneg=True),
                      avg_price=dec(o, 'avgPrice', nonneg=True, optional=True),
                      cum_quote=dec(o, 'cumQuote', nonneg=True, optional=True),
                      price=dec(o, 'price', nonneg=True, optional=True),
                      stop_price=dec(o, 'stopPrice', nonneg=True, optional=True),
                      reduce_only=flag(o, 'reduceOnly'), close_position=flag(o, 'closePosition'),
                      working_type=text(o, 'workingType', optional=True),
                      update_time_ms=integer(o, 'updateTime', nonneg=True))
    if rec.executed_qty > rec.orig_qty:
        raise MalformedResponse('executedQty above origQty')
    if rec.status == 'FILLED' and rec.executed_qty != rec.orig_qty:
        raise MalformedResponse('FILLED with executedQty != origQty')
    return rec


ALGO_STATUSES = ('NEW', 'TRIGGERING', 'TRIGGERED', 'FINISHED', 'CANCELED', 'EXPIRED', 'REJECTED')
# TRIGGERING/TRIGGERED are NOT final: the conditional fired and a child order (actual_order_id) carries the fill.
FINAL_ALGO_STATUSES = ('FINISHED', 'CANCELED', 'EXPIRED', 'REJECTED')


@dataclass(frozen=True)
class AlgoOrderRecord:
    algo_id: int
    client_algo_id: str
    algo_type: str
    order_type: str
    symbol: str
    side: str
    position_side: str
    quantity: Decimal
    trigger_price: Decimal
    algo_status: str
    working_type: object
    reduce_only: bool
    close_position: bool
    create_time_ms: object
    update_time_ms: object
    actual_order_id: object         # str or None: the child order once triggered
    actual_price: object            # Decimal or None

    @property
    def is_final(self):
        return self.algo_status in FINAL_ALGO_STATUSES


def parse_algo_order(o):
    o = as_obj(o, 'algoOrder')
    actual = o.get('actualOrderId')
    if actual in (None, ''):
        actual = None
    elif isinstance(actual, bool) or not isinstance(actual, (int, str)):
        raise MalformedResponse('actualOrderId: unexpected type')
    else:
        actual = str(actual)
    return AlgoOrderRecord(algo_id=integer(o, 'algoId', positive=True), client_algo_id=text(o, 'clientAlgoId'),
                           algo_type=text(o, 'algoType'), order_type=text(o, 'orderType'), symbol=text(o, 'symbol'),
                           side=text(o, 'side', allowed=SIDES),
                           position_side=text(o, 'positionSide', allowed=POSITION_SIDES),
                           quantity=dec(o, 'quantity', nonneg=True), trigger_price=dec(o, 'triggerPrice', positive=True),
                           algo_status=text(o, 'algoStatus', allowed=ALGO_STATUSES),
                           working_type=text(o, 'workingType', optional=True),
                           reduce_only=flag(o, 'reduceOnly'), close_position=flag(o, 'closePosition'),
                           create_time_ms=integer(o, 'createTime', optional=True, nonneg=True),
                           update_time_ms=integer(o, 'updateTime', optional=True, nonneg=True),
                           actual_order_id=actual, actual_price=dec(o, 'actualPrice', nonneg=True, optional=True))


def parse_open_orders(data):
    return tuple(parse_order(o) for o in as_list(data, 'openOrders'))


def parse_open_algo_orders(data):
    """Legacy accepted a list, or an object whose 'orders' is a list. Anything else is malformed (unknown != empty)."""
    if isinstance(data, dict):
        data = data.get('orders')
    return tuple(parse_algo_order(o) for o in as_list(data, 'openAlgoOrders'))


# ---------- fills ----------

@dataclass(frozen=True)
class Fill:
    trade_id: int
    order_id: int
    symbol: str
    side: str
    position_side: str
    price: Decimal
    qty: Decimal
    quote_qty: Decimal
    commission: Decimal
    commission_asset: str
    realized_pnl: Decimal
    maker: bool
    buyer: bool
    time_ms: int


def parse_fills(data):
    out, seen = [], set()
    for t in as_list(data, 'userTrades'):
        f = Fill(trade_id=integer(t, 'id', positive=True), order_id=integer(t, 'orderId', positive=True),
                 symbol=text(t, 'symbol'), side=text(t, 'side', allowed=SIDES),
                 position_side=text(t, 'positionSide', allowed=POSITION_SIDES),
                 price=dec(t, 'price', positive=True), qty=dec(t, 'qty', positive=True),
                 quote_qty=dec(t, 'quoteQty', nonneg=True), commission=dec(t, 'commission'),
                 commission_asset=text(t, 'commissionAsset'), realized_pnl=dec(t, 'realizedPnl'),
                 maker=flag(t, 'maker'), buyer=flag(t, 'buyer'), time_ms=integer(t, 'time', positive=True))
        if f.trade_id in seen:
            raise MalformedResponse('duplicate trade id')
        seen.add(f.trade_id)
        out.append(f)
    return tuple(out)


# ---------- income (funding / commission / realized pnl) ----------

INCOME_TYPE_RE = re.compile(r'^[A-Z][A-Z_]{1,39}$')


@dataclass(frozen=True)
class IncomeRow:
    """One /fapi/v1/income row. income is SIGNED as Binance reports it: negative = paid by the account (commission,
    funding paid), positive = received. symbol is None for account-level rows (e.g. TRANSFER). trade_id is None when
    Binance sends "" (funding, transfers). Unknown income types are kept (Binance adds types); summaries file them
    under their own name, never under funding/commission/pnl."""
    symbol: object
    income_type: str
    income: Decimal
    asset: str
    info: str
    time_ms: int
    tran_id: int
    trade_id: object

    @property
    def key(self):
        return (self.tran_id, self.income_type, self.asset)


def parse_income(data):
    out, seen = [], set()
    for r in as_list(data, 'income'):
        r = as_obj(r, 'income row')
        sym = r.get('symbol')
        if sym not in ('', None) and not isinstance(sym, str):
            raise MalformedResponse('symbol: not text')
        trade = r.get('tradeId')
        if isinstance(trade, bool) or not isinstance(trade, (str, int, type(None))):
            raise MalformedResponse('tradeId: unexpected type')
        info = r.get('info', '')
        if not isinstance(info, str):
            raise MalformedResponse('info: not text')
        itype = text(r, 'incomeType')
        if not INCOME_TYPE_RE.fullmatch(itype):
            raise MalformedResponse('incomeType: unexpected value')
        row = IncomeRow(symbol=sym or None, income_type=itype, income=dec(r, 'income'), asset=text(r, 'asset'),
                        info=info, time_ms=integer(r, 'time', positive=True),
                        tran_id=integer(r, 'tranId', positive=True),
                        trade_id=None if trade in ('', None) else str(trade))
        if row.key in seen:
            raise MalformedResponse('duplicate income row')
        seen.add(row.key)
        out.append(row)
    return tuple(out)


@dataclass(frozen=True)
class MarkPrice:
    symbol: str
    mark_price: Decimal
    index_price: Decimal
    time_ms: int                  # Binance server time of the mark


def parse_premium_index(data):
    """/fapi/v1/premiumIndex?symbol=X (one object). A list (no symbol sent) is refused as malformed."""
    o = as_obj(data, 'premiumIndex')
    return MarkPrice(symbol=text(o, 'symbol'), mark_price=dec(o, 'markPrice', positive=True),
                     index_price=dec(o, 'indexPrice', nonneg=True), time_ms=integer(o, 'time', positive=True))


def parse_server_time(data):
    return server_ms(as_obj(data, 'time'), 'serverTime')
