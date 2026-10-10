"""Typed outcomes of one transport call. They hold no credentials and no signed URL.

Order operations (place / query / cancel) -> OrderOutcome:
  KNOWN         the venue answered with a live record (NEW, PARTIALLY_FILLED; algo NEW/TRIGGERING/TRIGGERED)
  FINAL         the venue answered with a terminal record (FILLED, CANCELED, EXPIRED, EXPIRED_IN_MATCH, REJECTED;
                algo FINISHED/CANCELED/EXPIRED/REJECTED). Only this kind carries an executed quantity that can be trusted.
  ACKNOWLEDGED  the venue accepted the request but sent no record (algo cancel {"code":"200","msg":"success"}).
                Confirm with a query.
  REJECTED      Binance refused the request with an error code: no order was created / changed by THIS request.
  UNKNOWN       timeout, connection drop, 5xx, an ambiguous code (-1000/-1001/-1006/-1007), an unreadable answer, or
                an answer that does not echo our client id. Mirrors legacy AmbiguousOrder: the order may exist.
  NOT_FOUND     Binance cannot find the order (-2011/-2013). Carries NotFoundEvidence; it is NOT "never filled".

Reads -> ReadOutcome: OK (value parsed) / REJECTED (error code) / UNKNOWN (no trustworthy data; never "empty").
"""
from dataclasses import dataclass
from enum import Enum

from .wire import RateLimitUsage


class OrderOutcomeKind(Enum):
    KNOWN = 'known'
    FINAL = 'final'
    ACKNOWLEDGED = 'acknowledged'
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'
    NOT_FOUND = 'not_found'


class ReadKind(Enum):
    OK = 'ok'
    REJECTED = 'rejected'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class OrderOutcome:
    kind: OrderOutcomeKind
    operation: str                  # 'place_market' | 'place_stop_market' | 'query' | 'cancel' (+ '_algo' variants)
    endpoint: str                   # 'classic' | 'algo'
    client_id: str                  # the id we sent / looked up, verbatim
    record: object = None           # OrderRecord | AlgoOrderRecord | None
    error: object = None            # VenueError | None
    not_found: object = None        # NotFoundEvidence | None
    unknown_reason: object = None   # str | None (a stable token, never an exception message)
    http_status: object = None      # int | None (None when no answer arrived)
    rate: RateLimitUsage = RateLimitUsage()

    @property
    def is_final(self):
        return self.kind is OrderOutcomeKind.FINAL

    @property
    def executed_qty(self):
        """Only a FINAL record carries an executed quantity the ledger may book. Everything else is None."""
        if self.kind is OrderOutcomeKind.FINAL and self.record is not None:
            return getattr(self.record, 'executed_qty', None)
        return None


@dataclass(frozen=True)
class ReadOutcome:
    kind: ReadKind
    operation: str
    value: object = None
    error: object = None
    unknown_reason: object = None
    http_status: object = None
    rate: RateLimitUsage = RateLimitUsage()

    @property
    def ok(self):
        return self.kind is ReadKind.OK
