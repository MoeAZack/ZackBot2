"""Cassette field-path ALLOW-LIST (Cowork #37 R1): the recorder keeps a value only under a name it knows to be a plain
Binance USD-M field / parameter / header. Anything else - a new Binance field, an echoed header, a renamed or disguised
secret name in any script - is stored as <redacted> (fail closed), on top of the name deny-list and the value scrub.

JSON bodies are walked structurally (numbers kept as their exact text), not by regex: an unknown key's whole value,
object or list included, becomes "<redacted>". Nesting deeper than MAX_DEPTH is refused (the recorder marks the cassette
unproducible: typed CassetteLeak at to_json, never a RecursionError in the live HTTP path).
"""
import json

from .redact import REDACTED, is_sensitive_name

MAX_DEPTH = 32

PARAMS = frozenset({
    'symbol', 'side', 'positionSide', 'type', 'quantity', 'price', 'stopPrice', 'triggerPrice', 'newClientOrderId',
    'clientAlgoId', 'origClientOrderId', 'orderId', 'algoId', 'algoType', 'workingType', 'timeInForce', 'reduceOnly',
    'closePosition', 'interval', 'startTime', 'endTime', 'limit', 'fromId', 'incomeType', 'recvWindow', 'timestamp',
    'dualSidePosition', 'priceProtect', 'newOrderRespType', 'activatePrice', 'callbackRate', 'goodTillDate',
    'priceMatch', 'selfTradePreventionMode', 'pair', 'contractType'})

HEADERS = frozenset({'content-type', 'content-length', 'date', 'retry-after', 'server', 'connection'})
HEADER_PREFIXES = ('x-mbx-used-weight', 'x-mbx-order-count')

FIELDS = frozenset({
    # exchangeInfo
    'timezone', 'serverTime', 'rateLimits', 'rateLimitType', 'interval', 'intervalNum', 'limit', 'symbols', 'symbol',
    'pair', 'contractType', 'status', 'baseAsset', 'quoteAsset', 'marginAsset', 'pricePrecision', 'quantityPrecision',
    'baseAssetPrecision', 'quotePrecision', 'filters', 'filterType', 'minPrice', 'maxPrice', 'tickSize', 'minQty',
    'maxQty', 'stepSize', 'notional', 'multiplierUp', 'multiplierDown', 'multiplierDecimal', 'orderTypes',
    'timeInForce', 'triggerProtect', 'assets', 'deliveryDate', 'onboardDate', 'maintMarginPercent',
    'requiredMarginPercent', 'underlyingType', 'underlyingSubType', 'settlePlan', 'liquidationFee', 'marketTakeBound',
    'maxMoveOrderLimit', 'exchangeFilters', 'asset', 'autoAssetExchange',
    # account / positions / balances
    'feeTier', 'canTrade', 'canDeposit', 'canWithdraw', 'updateTime', 'multiAssetsMargin', 'tradeGroupId',
    'totalInitialMargin', 'totalMaintMargin', 'totalWalletBalance', 'totalUnrealizedProfit', 'totalMarginBalance',
    'totalPositionInitialMargin', 'totalOpenOrderInitialMargin', 'totalCrossWalletBalance', 'totalCrossUnPnl',
    'availableBalance', 'maxWithdrawAmount', 'walletBalance', 'unrealizedProfit', 'marginBalance', 'maintMargin',
    'initialMargin', 'positionInitialMargin', 'openOrderInitialMargin', 'crossWalletBalance', 'crossUnPnl',
    'marginAvailable', 'positions', 'leverage', 'isolated', 'entryPrice', 'breakEvenPrice', 'maxNotional',
    'positionSide', 'positionAmt', 'isolatedWallet', 'bidNotional', 'askNotional', 'markPrice', 'unRealizedProfit',
    'liquidationPrice', 'marginType', 'isolatedMargin', 'isAutoAddMargin', 'maxNotionalValue', 'adl',
    'dualSidePosition',
    # orders / algo orders / trades / income / errors
    'orderId', 'clientOrderId', 'price', 'avgPrice', 'origQty', 'executedQty', 'cumQty', 'cumQuote', 'type',
    'origType', 'reduceOnly', 'closePosition', 'side', 'stopPrice', 'workingType', 'priceProtect', 'priceMatch',
    'selfTradePreventionMode', 'goodTillDate', 'time', 'activatePrice', 'priceRate', 'callbackRate', 'algoId',
    'clientAlgoId', 'algoType', 'orderType', 'algoStatus', 'triggerPrice', 'quantity', 'icebergQuantity',
    'createTime', 'triggerTime', 'actualOrderId', 'actualPrice', 'code', 'msg', 'orders', 'buyer', 'commission',
    'commissionAsset', 'id', 'maker', 'qty', 'quoteQty', 'realizedPnl', 'income', 'incomeType', 'info', 'tranId',
    'tradeId', 'total', 'rows',
    # premiumIndex
    'indexPrice', 'estimatedSettlePrice', 'lastFundingRate', 'interestRate', 'nextFundingTime'})


def param_allowed(name):
    return name in PARAMS and not is_sensitive_name(name)


def header_allowed(name):
    n = str(name).lower()
    return (n in HEADERS or n.startswith(HEADER_PREFIXES)) and not is_sensitive_name(n)


def field_allowed(name):
    return name in FIELDS and not is_sensitive_name(name)


class TooDeep(Exception):
    pass


class _Num(str):
    """A JSON number kept as its exact text."""


def _dump(o, depth=0):
    if depth > MAX_DEPTH:
        raise TooDeep
    if isinstance(o, dict):
        return '{' + ','.join(json.dumps(k, ensure_ascii=False) + ':' + _dump(v, depth + 1) for k, v in o.items()) + '}'
    if isinstance(o, list):
        return '[' + ','.join(_dump(v, depth + 1) for v in o) + ']'
    if isinstance(o, _Num):
        return str(o)
    return json.dumps(o, ensure_ascii=False)


def _walk(o, learn, depth=0):
    if depth > MAX_DEPTH:
        raise TooDeep
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if field_allowed(k):
                out[k] = _walk(v, learn, depth + 1)
            else:
                if is_sensitive_name(k):
                    _learn_all(v, learn)
                out[k] = REDACTED
        return out
    if isinstance(o, list):
        return [_walk(v, learn, depth + 1) for v in o]
    return o


def _learn_all(v, learn, depth=0):
    if depth > MAX_DEPTH:
        return
    if isinstance(v, str):
        learn(v)
    elif isinstance(v, dict):
        for x in v.values():
            _learn_all(x, learn, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _learn_all(x, learn, depth + 1)


def sanitize_json(text, learn):
    """The allow-listed JSON text, or None when `text` is not a JSON object / array. Raises TooDeep."""
    s = text.strip()
    if not s or s[0] not in '{[':
        return None
    try:
        doc = json.loads(s, parse_float=_Num, parse_int=_Num)
    except RecursionError:
        raise TooDeep from None
    except ValueError:
        return None
    try:
        return _dump(_walk(doc, learn))
    except RecursionError:
        raise TooDeep from None
