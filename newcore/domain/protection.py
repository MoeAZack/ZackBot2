"""Protective stops (contract invariant 8, ruling 8: one protection lifecycle).

A Protection holds only the protection's OWN facts: whose exposure it covers (`owner_id`), the level and size of the
order that currently carries it (`order`, a PROTECT OrderIntent), the `replacement` being placed, when it was last
confirmed on the exchange and what the verifier is chasing (`miss`). The lifecycle status is DERIVED by
`protection_status` from this record, the order intents and the exposure, so there is no second source of truth.

Bounds: coverage is reduce-only and side-correct (the carrying order has the position's symbol and side), and never
exceeds the exposure: a replacement is at most the exposure, and the active order is at most the exposure unless a
replacement (resize) is in flight. A replacement keeps the old order: the old one may be cancelled only after the new one
is confirmed, so a REPLACING protection never has its old order CANCELLING.
"""
from __future__ import annotations

import enum
from decimal import Decimal

from .base import Record, check_id, check_text, positive, record, req
from .orders import IntentState, OrderType, Purpose


class MissPhase(enum.StrEnum):
    CHECKING = 'checking'        # our stop is not listed; re-checking before acting
    RESTORING = 'restoring'      # it is gone; a replacement is being placed
    OWNER_CHECK = 'owner_check'  # a foreign order covers the position, or our own stop is unreadable


class ProtectionStatus(enum.StrEnum):
    NEEDS_PLACEMENT = 'needs_placement'
    PLACEMENT_PENDING = 'placement_pending'
    OWNED_UNVERIFIED = 'owned_unverified'
    OWNED_CONFIRMED = 'owned_confirmed'
    REPLACING = 'replacing'                  # old order still working; the replacement is not confirmed yet
    UNDERSIZED = 'undersized'                # working, but covers less than the exposure
    RELEASING = 'releasing'                  # its order is being cancelled
    MISSING_CHECKING = 'missing_checking'
    MISSING_RESTORING = 'missing_restoring'
    OWNER_CHECK = 'owner_check'


PROTECTING = frozenset({ProtectionStatus.OWNED_UNVERIFIED, ProtectionStatus.OWNED_CONFIRMED, ProtectionStatus.REPLACING})
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
            check_text(self.foreign_order_id, p + '.foreign_order_id', 64)


@record
class Protection(Record):
    owner_id: str                    # lot_ (a lot's stop) or int_ (provisional stop of an unconfirmed market ENTRY)
    price: Decimal                   # level of the carrying order (or of the placement to make)
    qty: Decimal                     # size of the carrying order (or of the placement to make)
    order: str | None = None         # int_ of the PROTECT intent carrying it now
    replacement: str | None = None   # int_ of the PROTECT intent that will replace it
    confirmed_at_ms: int | None = None
    miss: StopMiss | None = None

    def _validate(self, p):
        p = f'{p}[{self.owner_id}]'
        check_id(self.owner_id, p + '.owner_id', 'lot', 'int')
        positive(self.price, p + '.price')
        positive(self.qty, p + '.qty')
        for f in ('order', 'replacement'):
            if getattr(self, f) is not None:
                check_id(getattr(self, f), f'{p}.{f}', 'int')
        req(self.replacement is None or self.replacement != self.order, p + '.replacement', 'replaces itself')
        req(self.confirmed_at_ms is None or self.miss is None, p + '.confirmed_at_ms', 'confirmed and missing at once')
        req(self.confirmed_at_ms is None or self.order is not None, p + '.confirmed_at_ms', 'confirmed without an order')
        if self.miss is not None and self.miss.phase is MissPhase.CHECKING:
            req(self.order is not None, p + '.miss', 'CHECKING re-checks an order we placed')


def protection_status(prot, intents_by_id, exposure):
    """The one derived lifecycle status. `intents_by_id` = the portfolio's open intents; `exposure` = what it protects."""
    if prot.miss is not None:
        return _MISS_STATUS[prot.miss.phase]
    if prot.order is None:
        return ProtectionStatus.PLACEMENT_PENDING if prot.replacement is not None else ProtectionStatus.NEEDS_PLACEMENT
    st = intents_by_id[prot.order].state
    if st is IntentState.CANCELLING:
        return ProtectionStatus.RELEASING
    if st is not IntentState.WORKING:
        return ProtectionStatus.PLACEMENT_PENDING
    if prot.replacement is not None:
        return ProtectionStatus.REPLACING
    if prot.qty < exposure:
        return ProtectionStatus.UNDERSIZED
    return ProtectionStatus.OWNED_CONFIRMED if prot.confirmed_at_ms is not None else ProtectionStatus.OWNED_UNVERIFIED


def _carrier(intents_by_id, iid, prot, side, symbol, path):
    it = intents_by_id.get(iid)
    req(it is not None, path, 'names no open intent of this portfolio')
    req(it.purpose is Purpose.PROTECT and it.order_type is OrderType.STOP_MARKET and it.owner_id == prot.owner_id,
        path, 'not a PROTECT intent of this owner')
    req((it.symbol, it.side) == (symbol, side), path, 'reduce-only coverage of another symbol / side')
    return it


def check_protection(prot, intents_by_id, side, symbol, exposure, path):
    """Cross-record rules: carrying / replacement orders resolve to this owner's PROTECT intents, match the record, are
    side-correct, and never exceed the exposure."""
    if prot.order is not None:
        it = _carrier(intents_by_id, prot.order, prot, side, symbol, path + '.order')
        req((it.qty, it.stop_price) == (prot.qty, prot.price), path + '.order', 'the stop order differs from the protection')
        if prot.confirmed_at_ms is not None:
            req(it.state is IntentState.WORKING, path + '.confirmed_at_ms', 'only a WORKING order is confirmed')
        if prot.miss is not None and prot.miss.phase is MissPhase.CHECKING:
            req(it.state is IntentState.WORKING, path + '.miss', 'CHECKING re-checks a WORKING order')
    if prot.replacement is not None:
        new = _carrier(intents_by_id, prot.replacement, prot, side, symbol, path + '.replacement')
        req(new.qty <= exposure, path + '.replacement', f'replacement {new.qty} exceeds the exposure {exposure}')
        if prot.order is not None:
            req(intents_by_id[prot.order].state is not IntentState.CANCELLING, path + '.order',
                'the old protection is kept until the replacement is confirmed')
    req(prot.qty <= exposure or prot.replacement is not None, path + '.qty',
        f'protection {prot.qty} exceeds the exposure {exposure} with no resize in flight')
