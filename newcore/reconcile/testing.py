"""FaultVenue: a VenuePort wrapper over FakeVenue that injects exchange-side faults and changes made outside the bot.

TEST / HARNESS SEAM ONLY. The runner never builds one; it exists so the REC-02 matrix (and a TNET-01 dry run) can drive
the REAL runner fold through every row on FakeVenue. It reaches into FakeVenue's own bookkeeping (`_apply_fill`,
`_orders`) to model what an operator or the exchange does outside the bot, so it only wraps a FakeVenue.

Outside changes (the venue really changes; every later read and fill shows it):
  manual_close(symbol, side, qty)         the owner closes / reduces a position by hand (a fill of a foreign order id)
  manual_add(symbol, side, qty)           the owner adds by hand; foreign_position is the same on a side the bot never held
  foreign_order(client_id, ...)           an order the bot did not place is listed among the open orders
  vanish_stop(client_id)                  a resting order disappears (cancelled / expired outside the bot)
  move_stop(client_id, stop_price)        a resting stop is edited on the exchange
Answer faults (the venue is unchanged; only what the bot is told differs):
  not_found_until(client_id, until_ms)    by-id queries answer NOT_FOUND (-2013) while the venue clock is before until_ms
  hide_position_until(symbol, side, ms)   position reads show that side flat while the clock is before ms (read lag)
  stale_reads(n, age_ms)                  the next n position / open-order reads carry a timestamp age_ms in the past
  skew_fills(eoid, qty=, price=)          the fills of that exchange order id disagree with its order record
  fake_cancel(client_id)                  the next cancel answers CANCELED while the order keeps resting (a wrong ack)
  partial_next_entry(qty)                 the next opening market order executes only qty and ends EXPIRED
Read for the reconciliation (not on the port; the runner reads userTrades through its adapter):
  trades(symbol, side, from_ms) -> ReadOutcome of every VenueFill of that side at or after from_ms
"""
from __future__ import annotations

from decimal import Decimal

from newcore.ports.venue import (MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, VenueFill,
                                 VenueOrder, VenuePosition)

ZERO = Decimal(0)
E_NO_ORDER = -2013
MANUAL_EOID_BASE = 880_000


class FaultVenue:
    __test__ = False

    def __init__(self, inner):
        self.inner = inner
        self._foreign = []
        self._nf_until = {}
        self._hide = {}
        self._stale = [0, 0]
        self._skew = {}
        self._fake_cancel = set()
        self._partial = None
        self._expired = set()
        self.manual_eoids = []

    def __getattr__(self, name):                 # clock, equity, funding, advance_to, ... of the FakeVenue
        return getattr(self.inner, name)

    @property
    def now(self):
        return self.inner.now_ms

    # ------------------------------------------------------------------------------------------------ outside changes
    def _price(self, symbol, price):
        if price is not None:
            return Decimal(price)
        bar = self.inner.bar(symbol, self.now)
        if bar is not None:
            return bar.open
        return self.inner.bar(symbol, self.now - self.inner.tf_ms).close

    def _manual_fill(self, symbol, side, qty, price, reduce):
        eoid = str(MANUAL_EOID_BASE + len(self.manual_eoids) + 1)
        self.manual_eoids.append(eoid)
        self.inner._apply_fill(symbol, side, Decimal(qty), self._price(symbol, price), reduce=reduce, eoid=eoid,
                               at_ms=self.now, fee=ZERO)
        return eoid

    def manual_close(self, symbol, side, qty, price=None):
        return self._manual_fill(symbol, side, qty, price, True)

    def manual_add(self, symbol, side, qty, price=None):
        return self._manual_fill(symbol, side, qty, price, False)

    foreign_position = manual_add

    def foreign_order(self, client_id, *, symbol, side, qty, reduce=False, order_type='LIMIT', stop_price=None,
                      route='classic', exchange_order_id=None):
        o = VenueOrder(ref=OrderRef(symbol=symbol, client_id=client_id, route=route),
                       exchange_order_id=exchange_order_id or str(990_000 + len(self._foreign) + 1),
                       position_side=side, reduce=reduce, order_type=order_type, status='NEW', qty=Decimal(qty),
                       close_position=False, stop_price=None if stop_price is None else Decimal(stop_price))
        self._foreign.append(o)
        return o

    def vanish_stop(self, client_id):
        self.inner.external_cancel(client_id)

    def move_stop(self, client_id, stop_price):
        o = self.inner._orders[client_id]
        if o.status != 'NEW' or o.order_type != 'STOP_MARKET':
            raise ValueError('not a resting stop')
        o.stop_price = Decimal(stop_price)

    # ------------------------------------------------------------------------------------------------ answer faults
    def not_found_until(self, client_id, until_ms):
        self._nf_until[client_id] = until_ms

    def hide_position_until(self, symbol, side, until_ms):
        self._hide[(symbol, side)] = until_ms

    def stale_reads(self, n, age_ms):
        self._stale = [n, age_ms]

    def skew_fills(self, eoid, *, qty=None, price=None):
        self._skew[eoid] = (None if qty is None else Decimal(qty), None if price is None else Decimal(price))

    def fake_cancel(self, client_id):
        self._fake_cancel.add(client_id)

    def partial_next_entry(self, qty):
        self._partial = Decimal(qty)

    # ------------------------------------------------------------------------------------------------ VenuePort
    def _status(self, out):
        if out.kind is OutcomeKind.FINAL and out.ref.client_id in self._expired:
            return OrderOutcome(kind=out.kind, ref=out.ref, observed_at_ms=out.observed_at_ms, status='EXPIRED',
                                exchange_order_id=out.exchange_order_id, executed_qty=out.executed_qty,
                                avg_price=out.avg_price, detail=out.detail)
        return out

    def submit_market(self, order: MarketOrder) -> OrderOutcome:
        if self._partial is not None and not order.reduce:
            qty, self._partial = min(self._partial, order.qty), None
            self._expired.add(order.ref.client_id)
            order = MarketOrder(ref=order.ref, position_side=order.position_side, qty=qty, reduce=False)
        return self._status(self.inner.submit_market(order))

    def submit_stop(self, order):
        return self.inner.submit_stop(order)

    def cancel(self, ref):
        if ref.client_id in self._fake_cancel:
            self._fake_cancel.discard(ref.client_id)
            q = self.inner.query(ref)
            if q.kind is OutcomeKind.KNOWN:
                return OrderOutcome(kind=OutcomeKind.FINAL, ref=ref, observed_at_ms=self.now, status='CANCELED',
                                    exchange_order_id=q.exchange_order_id, executed_qty=ZERO)
        return self.inner.cancel(ref)

    def query(self, ref):
        until = self._nf_until.get(ref.client_id)
        if until is not None and self.now < until:
            return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=self.now, error_code=E_NO_ORDER)
        return self._status(self.inner.query(ref))

    def _stamp(self, r):
        n, age = self._stale
        if n <= 0 or r.kind is not ReadKind.OK:
            return r
        return ReadOutcome(kind=r.kind, observed_at_ms=r.observed_at_ms - age, value=r.value)

    def _spend_stale(self):
        if self._stale[0] > 0:
            self._stale[0] -= 1

    def positions(self, symbol=None):
        r = self.inner.positions(symbol)
        if r.kind is ReadKind.OK and self._hide:
            rows = []
            for p in r.value:
                until = self._hide.get((p.symbol, p.side))
                if until is not None and self.now < until:
                    p = VenuePosition(symbol=p.symbol, side=p.side, qty=ZERO, entry_price=ZERO)
                rows.append(p)
            r = ReadOutcome(kind=r.kind, observed_at_ms=r.observed_at_ms, value=tuple(rows))
        out = self._stamp(r)
        self._spend_stale()
        return out

    def open_orders(self, symbol=None):
        r = self.inner.open_orders(symbol)
        if r.kind is ReadKind.OK and self._foreign:
            extra = tuple(o for o in self._foreign if symbol is None or o.ref.symbol == symbol)
            r = ReadOutcome(kind=r.kind, observed_at_ms=r.observed_at_ms, value=r.value + extra)
        out = self._stamp(r)
        self._spend_stale()
        return out

    def fills(self, symbol, exchange_order_id):
        r = self.inner.fills(symbol, exchange_order_id)
        skew = self._skew.get(exchange_order_id)
        if skew is None or r.kind is not ReadKind.OK or not r.value:
            return r
        f = r.value[0]
        qty, price = skew
        row = VenueFill(trade_id=f.trade_id, exchange_order_id=f.exchange_order_id, symbol=f.symbol,
                        position_side=f.position_side, qty=qty if qty is not None else f.qty,
                        price=price if price is not None else f.price, fee=f.fee, fee_asset=f.fee_asset,
                        realized_pnl=f.realized_pnl, maker=f.maker, at_ms=f.at_ms)
        return ReadOutcome(kind=r.kind, observed_at_ms=r.observed_at_ms, value=(row,))

    def trades(self, symbol, side, from_ms):
        rows = tuple(f for f in self.inner._fills if (f.symbol, f.position_side) == (symbol, side)
                     and f.at_ms >= from_ms)
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=self.now, value=rows)
