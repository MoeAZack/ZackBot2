"""Incident detail as a CLOSED vocabulary (follow-up to PR #44, Cowork 2).

An Incident's `detail` is never free text. It is an IncidentDetail: one registered DetailCode plus exactly the typed,
bounded fields that code defines (quantities and prices as canonical Decimals, a bounded count, a UTC-ms time, an
exchange status). Nothing a caller types, copies from a venue payload or an exception can reach the journal through
it, so a secret - however it is split or disguised - cannot be journaled as incident detail BY CONSTRUCTION (the
earlier key-shape heuristic was evadable with separators). Display text is rendered from the code's MEANING and the
fields, outside the domain.

The DetailCode registry is APPEND-ONLY within a schema version, like ReasonCode: a value is never renamed, reused or
deleted; the declaration order is pinned by tests/newcore/detail_codes_v1.txt, and every code has its MEANING and its
field set in DETAIL_FIELDS.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import Record, non_negative, positive, record, req
from .orders import ExchangeStatus

MAX_COUNT = 1_000_000


class DetailCode(enum.StrEnum):
    VENUE_FLAT_JOURNAL_OPEN = 'venue_flat_journal_open'
    VENUE_SMALLER_THAN_JOURNAL = 'venue_smaller_than_journal'
    VENUE_LARGER_THAN_JOURNAL = 'venue_larger_than_journal'
    FOREIGN_ORDERS = 'foreign_orders'
    STALE_READ = 'stale_read'
    LATE_FILL = 'late_fill'
    STOP_MISSING = 'stop_missing'
    STOP_UNEXPECTED_STATUS = 'stop_unexpected_status'
    EMERGENCY_CLOSE_PLACED = 'emergency_close_placed'
    JOURNAL_UNAVAILABLE = 'journal_unavailable'


_C = DetailCode
DETAIL_MEANING = {
    _C.VENUE_FLAT_JOURNAL_OPEN: 'the venue holds no position while the journal holds journal_qty open',
    _C.VENUE_SMALLER_THAN_JOURNAL: 'the venue holds venue_qty, less than the journal_qty the journal holds',
    _C.VENUE_LARGER_THAN_JOURNAL: 'the venue holds venue_qty, more than the journal_qty the journal holds',
    _C.FOREIGN_ORDERS: 'count orders on the venue that no journaled intent owns',
    _C.STALE_READ: 'a venue read taken at at_ms is older than the newest recorded result',
    _C.LATE_FILL: 'an owned order filled venue_qty at price after it was corroborated not-found',
    _C.STOP_MISSING: 'the owned protective stop is not listed on the venue',
    _C.STOP_UNEXPECTED_STATUS: 'the owned protective stop is listed with exchange_status',
    _C.EMERGENCY_CLOSE_PLACED: 'an emergency reduce-only close of journal_qty was placed after protection failed',
    _C.JOURNAL_UNAVAILABLE: 'the journal could not make an event durable',
}
# exactly the fields each code carries (every other field is None)
DETAIL_FIELDS = {
    _C.VENUE_FLAT_JOURNAL_OPEN: frozenset({'journal_qty'}),
    _C.VENUE_SMALLER_THAN_JOURNAL: frozenset({'venue_qty', 'journal_qty'}),
    _C.VENUE_LARGER_THAN_JOURNAL: frozenset({'venue_qty', 'journal_qty'}),
    _C.FOREIGN_ORDERS: frozenset({'count'}),
    _C.STALE_READ: frozenset({'at_ms'}),
    _C.LATE_FILL: frozenset({'venue_qty', 'price'}),
    _C.STOP_MISSING: frozenset(),
    _C.STOP_UNEXPECTED_STATUS: frozenset({'exchange_status'}),
    _C.EMERGENCY_CLOSE_PLACED: frozenset({'journal_qty'}),
    _C.JOURNAL_UNAVAILABLE: frozenset(),
}
del _C
VALUE_FIELDS = ('venue_qty', 'journal_qty', 'price', 'count', 'at_ms', 'exchange_status')


@record
class IncidentDetail(Record):
    code: DetailCode
    venue_qty: Decimal | None                 # a quantity the venue reports (>= 0)
    journal_qty: Decimal | None               # a quantity the journal holds (> 0)
    price: Decimal | None                     # a venue price (> 0)
    count: int | None                         # a bounded count (1 .. MAX_COUNT)
    at_ms: int | None                         # a UTC-ms time
    exchange_status: ExchangeStatus | None    # a venue order status

    def _validate(self, p):
        allowed = DETAIL_FIELDS[self.code]
        present = {f for f in VALUE_FIELDS if getattr(self, f) is not None}
        req(present == allowed, p + '.code',
            f'{self.code} carries exactly {sorted(allowed) or "no fields"}, not {sorted(present) or "none"}')
        if self.venue_qty is not None:
            non_negative(self.venue_qty, p + '.venue_qty')
        if self.journal_qty is not None:
            positive(self.journal_qty, p + '.journal_qty')
        if self.price is not None:
            positive(self.price, p + '.price')
        if self.count is not None:
            req(1 <= self.count <= MAX_COUNT, p + '.count', f'1..{MAX_COUNT}')
        if self.code is DetailCode.VENUE_SMALLER_THAN_JOURNAL:
            req(self.venue_qty < self.journal_qty, p + '.venue_qty', 'not smaller than the journal')
        if self.code is DetailCode.VENUE_LARGER_THAN_JOURNAL:
            req(self.venue_qty > self.journal_qty, p + '.venue_qty', 'not larger than the journal')
        if self.code is DetailCode.LATE_FILL:
            req(self.venue_qty > 0, p + '.venue_qty', 'a late fill executed something')
