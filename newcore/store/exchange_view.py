"""VenueExchangeView: the store's ExchangeView over a step-0 VenuePort's reads (positions + open orders).

The store never calls the exchange itself (design 1); the runner hands it this adapter. snapshot() reads positions and
open orders once each; an UNKNOWN or REJECTED read is ExchangeDown (an unknown read is never an empty account), so boot
falls into rule 5 (HOLD, no INIT, no promotion). taken_ms is the later of the two reads' observed_at_ms.

    view = VenueExchangeView(reads, key_digest=cfg.key_digest, steps={s: rules[s].step_size for s in symbols})
"""
from __future__ import annotations

from decimal import Decimal

from newcore.ports.venue import ReadKind

from .reconcile import ExchangeDown, ExchangeSnapshot, ExOrder, ExPosition

WORKING = frozenset({'NEW', 'PARTIALLY_FILLED'})
INFINITE = Decimal('Infinity')


def _kind(order_type):
    t = order_type.upper()
    return 'stop' if 'STOP' in t else 'limit' if 'LIMIT' in t else 'market'


class VenueExchangeView:
    def __init__(self, reads, *, key_digest, steps, symbol=None):
        self.reads, self.key_digest, self.steps, self.symbol = reads, key_digest, dict(steps), symbol

    def snapshot(self):
        pos = self.reads.positions(self.symbol)
        if pos.kind is not ReadKind.OK:
            raise ExchangeDown(f'positions read {pos.kind}')
        orders = self.reads.open_orders(self.symbol)
        if orders.kind is not ReadKind.OK:
            raise ExchangeDown(f'open orders read {orders.kind}')
        positions = tuple(ExPosition(p.symbol, p.side, p.qty) for p in pos.value if p.qty)
        out = tuple(ExOrder(o.ref.client_id, o.ref.symbol, o.position_side,
                            INFINITE if o.close_position else o.qty, bool(o.reduce or o.close_position),
                            _kind(o.order_type), 'working' if o.status.upper() in WORKING else o.status.lower())
                    for o in orders.value)
        return ExchangeSnapshot(key_digest=self.key_digest, taken_ms=max(pos.observed_at_ms, orders.observed_at_ms),
                                positions=positions, orders=out, steps=self.steps)
