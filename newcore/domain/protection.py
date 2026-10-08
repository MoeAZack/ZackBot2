"""Protective stops (ruling 8: one protection lifecycle).

A Protection holds only the protection's OWN facts: whose risk it covers, at what level and size, which PROTECT
OrderIntent carries it (`order`), when it was last confirmed on the exchange and what the verifier is chasing (`miss`).
The lifecycle status is DERIVED by `protection_status` from this record plus the order intent's state, so there is no
second source of truth (legacy kept stop_dirty / stop_id / prov_pending / stop_miss_why side by side).
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import CID_RE, Record, check_id, positive, record, req
from .orders import CANCEL_STATES, IntentState, OrderType, Purpose


class MissPhase(enum.StrEnum):
    CHECKING = 'checking'        # our stop is not listed; re-checking before acting
    RESTORING = 'restoring'      # it is gone; a replacement is being placed
    OWNER_CHECK = 'owner_check'  # a foreign order covers the position, or our own stop is unreadable


class ProtectionStatus(enum.StrEnum):
    NEEDS_PLACEMENT = 'needs_placement'
    PLACEMENT_PENDING = 'placement_pending'
    OWNED_UNVERIFIED = 'owned_unverified'
    OWNED_CONFIRMED = 'owned_confirmed'
    RELEASING = 'releasing'                  # its order is being cancelled (resize / lot finished)
    MISSING_CHECKING = 'missing_checking'
    MISSING_RESTORING = 'missing_restoring'
    OWNER_CHECK = 'owner_check'


PROTECTING = frozenset({ProtectionStatus.OWNED_UNVERIFIED, ProtectionStatus.OWNED_CONFIRMED})
_MISS_STATUS = {MissPhase.CHECKING: ProtectionStatus.MISSING_CHECKING, MissPhase.RESTORING: ProtectionStatus.MISSING_RESTORING,
                MissPhase.OWNER_CHECK: ProtectionStatus.OWNER_CHECK}


@record
class StopMiss(Record):
    phase: MissPhase
    count: int
    since_ms: int
    foreign_order_id: str | None = None      # the covering foreign order (OWNER_CHECK only)

    def _validate(self, p):
        req(self.count >= 1, p + '.count', 'a miss counts from 1')
        if self.foreign_order_id is not None:
            req(self.phase is MissPhase.OWNER_CHECK, p + '.foreign_order_id', 'only in OWNER_CHECK')
            req(0 < len(self.foreign_order_id) <= 64 and self.foreign_order_id.isprintable(), p + '.foreign_order_id', 'bad id')
            req(CID_RE.search(self.foreign_order_id) is None, p + '.foreign_order_id', 'a bot client id is never foreign')


@record
class Protection(Record):
    owner_id: str                    # lot_ (a lot's stop) or int_ (provisional stop of an unconfirmed market ENTRY)
    price: Decimal
    qty: Decimal
    order: str | None = None         # int_ of the PROTECT OrderIntent carrying it
    confirmed_at_ms: int | None = None
    miss: StopMiss | None = None

    def _validate(self, p):
        p = f'{p}[{self.owner_id}]'
        check_id(self.owner_id, p + '.owner_id', 'lot', 'int')
        positive(self.price, p + '.price')
        positive(self.qty, p + '.qty')
        if self.order is not None:
            check_id(self.order, p + '.order', 'int')
        req(self.confirmed_at_ms is None or self.miss is None, p + '.confirmed_at_ms', 'confirmed and missing at once')
        req(self.confirmed_at_ms is None or self.order is not None, p + '.confirmed_at_ms', 'confirmed without an order')
        if self.miss is not None and self.miss.phase is MissPhase.CHECKING:
            req(self.order is not None, p + '.miss', 'CHECKING re-checks an order we placed')


def protection_status(prot, intents_by_id):
    """The one derived lifecycle status. `intents_by_id` = the portfolio's open intents."""
    if prot.miss is not None:
        return _MISS_STATUS[prot.miss.phase]
    if prot.order is None:
        return ProtectionStatus.NEEDS_PLACEMENT
    st = intents_by_id[prot.order].state
    if st in CANCEL_STATES:
        return ProtectionStatus.RELEASING
    if st is IntentState.WORKING:
        return ProtectionStatus.OWNED_CONFIRMED if prot.confirmed_at_ms is not None else ProtectionStatus.OWNED_UNVERIFIED
    return ProtectionStatus.PLACEMENT_PENDING


def blocks_entries(prot, intents_by_id):
    return protection_status(prot, intents_by_id) not in PROTECTING


def check_protection(prot, intents_by_id, side, symbol, path):
    """Cross-record rules: the carrying order exists, is this protection's PROTECT intent and matches it exactly."""
    if prot.order is None:
        return
    it = intents_by_id.get(prot.order)
    req(it is not None, path + '.order', 'names no durable intent of this portfolio')
    req(it.purpose is Purpose.PROTECT and it.order_type is OrderType.STOP_MARKET and it.owner_id == prot.owner_id,
        path + '.order', 'not the PROTECT intent of this owner')
    req((it.symbol, it.side, it.qty, it.stop_price) == (symbol, side, prot.qty, prot.price), path + '.order',
        'the stop order differs from the protection (symbol / side / qty / price)')
    if prot.confirmed_at_ms is not None:
        req(it.state is IntentState.WORKING, path + '.confirmed_at_ms', 'only a WORKING order is confirmed')
    if prot.miss is not None and prot.miss.phase is MissPhase.CHECKING:
        req(it.state is IntentState.WORKING, path + '.miss', 'CHECKING re-checks a WORKING order')
