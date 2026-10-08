"""VenueExchangeView: the store's exchange snapshot from step-0 VenuePort reads (an unknown read is never empty)."""
from decimal import Decimal as D

import pytest

from nc02b_helpers import DIGEST, T, STEPS
from newcore.ports.venue import OrderRef, ReadKind, ReadOutcome, VenueOrder, VenuePosition
from newcore.store.exchange_view import VenueExchangeView
from newcore.store.reconcile import ExchangeDown

CID = 'zbn1o-' + 'a' * 26
FOREIGN = 'o99'


class Reads:
    def __init__(self, pos_kind=ReadKind.OK, ord_kind=ReadKind.OK, positions=(), orders=()):
        self.p, self.o = (pos_kind, positions), (ord_kind, orders)

    def _r(self, kind, value, at):
        if kind is ReadKind.OK:
            return ReadOutcome(kind=kind, observed_at_ms=at, value=tuple(value))
        if kind is ReadKind.REJECTED:
            return ReadOutcome(kind=kind, observed_at_ms=at, error_code=-1000)
        return ReadOutcome(kind=kind, observed_at_ms=at)

    def positions(self, symbol=None):
        return self._r(*self.p, T)

    def open_orders(self, symbol=None):
        return self._r(*self.o, T + 7)


def order(cid, qty, *, otype='STOP_MARKET', reduce=True, close=False, status='NEW', symbol='BTCUSDT'):
    return VenueOrder(ref=OrderRef(symbol=symbol, client_id=cid), exchange_order_id='1', position_side='LONG',
                      reduce=reduce, order_type=otype, status=status, qty=qty, close_position=close,
                      stop_price=D('95'))


def test_maps_positions_and_orders():
    reads = Reads(positions=[VenuePosition(symbol='BTCUSDT', side='LONG', qty=D('1'), entry_price=D('100')),
                             VenuePosition(symbol='BTCUSDT', side='SHORT', qty=D('0'), entry_price=D('0'))],
                  orders=[order(CID, D('1')), order(FOREIGN, D('0'), close=True),
                          order('zbn1o-' + 'b' * 26, D('2'), otype='LIMIT', reduce=False)])
    s = VenueExchangeView(reads, key_digest=DIGEST, steps=STEPS).snapshot()
    assert s.taken_ms == T + 7 and s.key_digest == DIGEST
    assert [(p.symbol, p.side, p.qty) for p in s.positions] == [('BTCUSDT', 'LONG', D('1'))]   # flat side dropped
    kinds = {o.client_id: (o.kind, o.reduce_only, o.status, o.qty) for o in s.orders}
    assert kinds[CID] == ('stop', True, 'working', D('1'))
    assert kinds[FOREIGN][0] == 'stop' and kinds[FOREIGN][3] == D('Infinity')                 # closePosition covers
    assert kinds['zbn1o-' + 'b' * 26][:2] == ('limit', False)


@pytest.mark.parametrize('which,kind', [('pos', ReadKind.UNKNOWN), ('pos', ReadKind.REJECTED),
                                        ('ord', ReadKind.UNKNOWN), ('ord', ReadKind.REJECTED)])
def test_an_unknown_or_rejected_read_is_exchange_down_never_empty(which, kind):
    reads = Reads(pos_kind=kind if which == 'pos' else ReadKind.OK, ord_kind=kind if which == 'ord' else ReadKind.OK)
    with pytest.raises(ExchangeDown):
        VenueExchangeView(reads, key_digest=DIGEST, steps=STEPS).snapshot()
