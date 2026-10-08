"""Binance USD-M error codes -> typed categories, and typed not-found evidence.

Sources: Binance USD-M Futures "Error Codes" page (shapes/names) and the legacy binance_client.py handling, studied for
behaviour only (never imported):
- AmbiguousOrder: 5xx, -1001, -1006, -1007 on a write = execution status unknown -> UNKNOWN here.
- -1021 is a clean rejection (the request was refused at the gateway; safe to resend after a time resync).
- -2011 / -2013 = "unknown order" / "order does not exist". Legacy cancel() treated these as "already gone" and
  get_order() as "never reached Binance". NC-01 invariant 7 rules a bare not-found AMBIGUOUS: Binance also answers
  -2013 for orders it has archived (e.g. CANCELED/EXPIRED without fills after ~3 days, any order after ~90 days), so it
  can never mean "never filled". It is carried here as NotFoundEvidence with no executed quantity.
- -4120 (and, per legacy, -1116 / -1102 / -4136 on a classic STOP_MARKET) = conditional orders must use the Algo
  Order API. The transport does NOT fall back by itself (no hidden second send); the caller re-routes.
- Legacy cancel() also treated -4120 as "already gone". That is NOT ported: a stop that must be handled by the algo
  service is not proven gone. Here -4120 is always ALGO_REQUIRED.
"""
from dataclasses import dataclass
from enum import Enum

from .redact import redact_values, scrub_tokens


class ErrorCategory(Enum):
    AMBIGUOUS = 'ambiguous'                       # execution status unknown -> UNKNOWN, never "failed"
    TIMESTAMP = 'timestamp'                       # -1021: rejected; resync clock and resend is safe
    AUTH = 'auth'                                 # key / signature / permission / IP
    RATE_LIMIT = 'rate_limit'                     # rejected by a limit (429/418/-1003/-1015); see Retry-After
    SERVER_BUSY = 'server_busy'                   # -1008 on a 4xx: rejected under load
    BAD_REQUEST = 'bad_request'                   # malformed / missing / not-required parameter, bad symbol
    FILTER = 'filter'                             # precision / tick / step / min-max qty / min notional
    ORDER_REJECTED = 'order_rejected'             # a business refusal of the order
    INSUFFICIENT_MARGIN = 'insufficient_margin'
    WOULD_TRIGGER = 'would_trigger'               # -2021: a stop on the wrong side of mark
    REDUCE_ONLY = 'reduce_only'                   # -2022: reduce-only refused (no position to reduce)
    NO_CHANGE = 'no_change'                       # setting already in place
    POSITION_MODE = 'position_mode'               # positionSide does not match the account's hedge/one-way mode
    ALGO_REQUIRED = 'algo_required'               # -4120: use the Algo Order API for conditional orders
    DUPLICATE_CLIENT_ID = 'duplicate_client_id'   # -4116: an order with this client id EXISTS (never "nothing done")
    NOT_FOUND = 'not_found'                       # -2011 / -2013 (see NotFoundEvidence)
    ENDPOINT_UNSUPPORTED = 'endpoint_unsupported' # -5000 / HTTP 404: the path does not exist here
    VENUE_RULE = 'venue_rule'                     # another -4xxx order rule
    UNMAPPED = 'unmapped'                         # a code this table does not know (still a 4xx rejection)


# code -> (Binance name, category). Names are Binance's where known.
ERROR_CODES = {
    -1000: ('UNKNOWN', ErrorCategory.AMBIGUOUS),
    -1001: ('DISCONNECTED', ErrorCategory.AMBIGUOUS),
    -1002: ('UNAUTHORIZED', ErrorCategory.AUTH),
    -1003: ('TOO_MANY_REQUESTS', ErrorCategory.RATE_LIMIT),
    -1006: ('UNEXPECTED_RESP', ErrorCategory.AMBIGUOUS),
    -1007: ('TIMEOUT', ErrorCategory.AMBIGUOUS),
    -1008: ('SERVER_BUSY', ErrorCategory.SERVER_BUSY),
    -1015: ('TOO_MANY_ORDERS', ErrorCategory.RATE_LIMIT),
    -1021: ('INVALID_TIMESTAMP', ErrorCategory.TIMESTAMP),
    -1022: ('INVALID_SIGNATURE', ErrorCategory.AUTH),
    -1102: ('MANDATORY_PARAM_EMPTY_OR_MALFORMED', ErrorCategory.BAD_REQUEST),
    -1106: ('PARAM_NOT_REQUIRED', ErrorCategory.BAD_REQUEST),
    -1111: ('BAD_PRECISION', ErrorCategory.FILTER),
    -1116: ('INVALID_ORDER_TYPE', ErrorCategory.BAD_REQUEST),
    -1121: ('BAD_SYMBOL', ErrorCategory.BAD_REQUEST),
    -2010: ('NEW_ORDER_REJECTED', ErrorCategory.ORDER_REJECTED),
    -2011: ('CANCEL_REJECTED', ErrorCategory.NOT_FOUND),
    -2013: ('NO_SUCH_ORDER', ErrorCategory.NOT_FOUND),
    -2014: ('BAD_API_KEY_FMT', ErrorCategory.AUTH),
    -2015: ('REJECTED_MBX_KEY', ErrorCategory.AUTH),
    -2018: ('BALANCE_NOT_SUFFICIENT', ErrorCategory.INSUFFICIENT_MARGIN),
    -2019: ('MARGIN_NOT_SUFFICIEN', ErrorCategory.INSUFFICIENT_MARGIN),
    -2021: ('ORDER_WOULD_IMMEDIATELY_TRIGGER', ErrorCategory.WOULD_TRIGGER),
    -2022: ('REDUCE_ONLY_REJECT', ErrorCategory.REDUCE_ONLY),
    -2027: ('MAX_LEVERAGE_RATIO', ErrorCategory.ORDER_REJECTED),
    -4003: ('QTY_LESS_THAN_ZERO', ErrorCategory.FILTER),
    -4005: ('QTY_GREATER_THAN_MAX_QTY', ErrorCategory.FILTER),
    -4014: ('PRICE_NOT_INCREASED_BY_TICK_SIZE', ErrorCategory.FILTER),
    -4023: ('QTY_NOT_INCREASED_BY_STEP_SIZE', ErrorCategory.FILTER),
    -4045: ('MAX_STOP_ORDER_EXCEEDED', ErrorCategory.ORDER_REJECTED),
    -4046: ('NO_NEED_TO_CHANGE_MARGIN_TYPE', ErrorCategory.NO_CHANGE),
    -4059: ('NO_NEED_TO_CHANGE_POSITION_SIDE', ErrorCategory.NO_CHANGE),
    -4061: ('POSITION_SIDE_NOT_MATCH', ErrorCategory.POSITION_MODE),
    -4116: ('DUPLICATED_CLIENT_ORDER_ID', ErrorCategory.DUPLICATE_CLIENT_ID),
    -4120: ('STOP_ORDER_SWITCH_ALGO', ErrorCategory.ALGO_REQUIRED),
    -4136: ('TARGET_STRATEGY_INVALID', ErrorCategory.BAD_REQUEST),
    -4164: ('MIN_NOTIONAL', ErrorCategory.FILTER),
    -5000: ('PATH_INVALID', ErrorCategory.ENDPOINT_UNSUPPORTED),
}

# Codes on which legacy binance_client.stop() switched a classic STOP_MARKET to POST /fapi/v1/algoOrder.
ALGO_FALLBACK_CODES = (-4120, -1116, -1102, -4136)

MSG_MAX = 240


def scrub_text(text, values=(), scrubber=None):
    """Venue-provided text: registered secret values are redacted (case-insensitive, percent-encoded, with '-', '_'
    or spaces inserted), then an optional scrubber runs, then long token-like runs are blanked INCLUDING runs split by
    '_', '-', '+', '/', '=' (Cowork finding 3), and the result is shortened. Redaction happens before truncation."""
    text = redact_values(str(text), values)
    if scrubber is not None:
        text = str(scrubber.scrub(text))
    return scrub_tokens(text)[:MSG_MAX]


def categorize(code):
    """(name, category) for a Binance error code. Unknown -4xxx -> VENUE_RULE; any other unknown -> UNMAPPED."""
    if code in ERROR_CODES:
        return ERROR_CODES[code]
    if isinstance(code, int) and -4999 <= code <= -4000:
        return ('UNLISTED_4XXX', ErrorCategory.VENUE_RULE)
    return ('UNLISTED', ErrorCategory.UNMAPPED)


@dataclass(frozen=True)
class VenueError:
    """A Binance refusal, as evidence: HTTP status, Binance code (None when the body had none), name, category, the
    scrubbed message, and whether a classic STOP_MARKET should be re-routed to the algo endpoint."""
    http_status: int
    code: object
    name: str
    category: ErrorCategory
    msg: str
    suggests_algo_route: bool = False

    @property
    def retryable_after_fix(self):
        """True when the request was cleanly refused for a transient reason (clock / rate / load)."""
        return self.category in (ErrorCategory.TIMESTAMP, ErrorCategory.RATE_LIMIT, ErrorCategory.SERVER_BUSY)


class NotFoundEvidenceType(Enum):
    QUERY_NO_SUCH_ORDER = 'query_no_such_order'           # GET /fapi/v1/order -> -2013
    CANCEL_UNKNOWN_ORDER = 'cancel_unknown_order'         # DELETE /fapi/v1/order -> -2011
    CANCEL_NO_SUCH_ORDER = 'cancel_no_such_order'         # DELETE /fapi/v1/order -> -2013
    ALGO_QUERY_NOT_FOUND = 'algo_query_not_found'         # GET /fapi/v1/algoOrder -> -2013 / "not exist" message
    ALGO_CANCEL_NOT_FOUND = 'algo_cancel_not_found'       # DELETE /fapi/v1/algoOrder -> -2011/-2013 / message


@dataclass(frozen=True)
class NotFoundEvidence:
    """Binance said it cannot find the order. This is NOT "never existed" and NOT "never filled" (NC-01 invariant 7):
    the order may have filled and been archived, or still be in flight. It carries no executed quantity; resolving it
    needs FINAL evidence (a later query, userTrades) or adoption corroborated by positions/orders."""
    evidence_type: NotFoundEvidenceType
    endpoint: str              # 'classic' | 'algo'
    operation: str             # 'query' | 'cancel'
    lookup_field: str          # 'origClientOrderId' | 'clientAlgoId'
    client_id: str
    code: object               # the Binance code (None if matched by message only)
    matched_by: str            # 'code' | 'message'
    msg: str

    executed_qty = None        # never a number: a not-found has no executed value
    proves_never_filled = False
