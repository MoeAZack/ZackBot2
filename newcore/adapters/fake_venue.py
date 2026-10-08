"""FakeVenue: a deterministic, candle-driven VenuePort (slice S1; STEP0_INTERFACE.md section 4).

Time. The venue has a clock `now_ms` that only `advance_to` moves, always to a candle boundary. `advance_to(t)` PLAYS
every candle that closes at or before t: funding is charged on the positions open when the candle starts, then every
resting stop is walked along the candle's zb-path/1 price path. Order calls happen BETWEEN candles: at now_ms, which is
the open of the next candle.

Fills (the golden pack's `legacy` cost model: tests/golden on origin/golden-short-mirrors, backtest.py):
  market          fills in full at the OPEN of the candle starting at now_ms, x (1 + slip) for a buy, x (1 - slip) for a
                  sell. A market order with no next candle (end of data) is REJECTED.
  STOP_MARKET     reduce-only, on the mark price = the path point. Gap: the candle OPENS through the stop -> filled at
                  the open. Otherwise the first path leg that reaches the stop fills it AT the stop. Either price then
                  takes the same taker slippage as a market fill (golden G-STOP-L-01: 98.02 x (1 - 0.0002)).
  fee             taker_fee x notional on every fill (charged to the wallet, reported positive in VenueFill.fee).
  funding         funding_per_bar x qty x candle close, charged per candle to every position open at the candle's start,
                  on BOTH sides as a cost (legacy flat model; research/ema_mom_after_cost.py charges it the same way).
zb-path/1 (backtest.path_points): green candle open -> low -> high -> close, red open -> high -> low -> close, doji
(c == o) adverse extreme last: a LONG position's path is open -> high -> low -> close, a SHORT's open -> low -> high ->
close. With one resting stop per position only "is the adverse extreme reached" matters; the walk orders several stops.

Hedge mode: positions are keyed by (symbol, position side). A reduce order never adds exposure: a reduce larger than
the position is REJECTED (-2022), as is a stop for a side with no position. A stop already through the mark when it is
placed is REJECTED (-2021, "would immediately trigger"). A client id is never reusable (-4116), so a re-send under the
same id can never fill twice. Unknown client ids answer NOT_FOUND (-2013 query / -2011 cancel); cancelling an order that
is no longer open answers REJECTED (-2011) and changes nothing.

Fault hooks (all off by default):
  lose_next_market_answer(truth)  the next market submit reaches the venue (truth 'filled': it executes; 'not_filled':
                                  nothing is created) but its answer is lost: UNKNOWN ('timeout').
  not_found(client_id, times)     the next `times` queries of that client id answer NOT_FOUND (visibility lag).
  unknown_reads(times)            the next `times` positions / open_orders reads answer UNKNOWN.
Test hooks: external_cancel(client_id) (a stop vanishes outside the bot), inject_position(...) (a foreign position).

Intra-candle play (M4, management marks; newcore/runner/intrabar.py drives it): begin_candle(t) charges the candle's
funding and opens it with the mark at its open; the caller walks the zb-path/1 path, moving the mark (set_mark) and
triggering resting stops at a level / at the gapped point (trigger_stop); while a candle is open a market order fills
at the CURRENT MARK (same slippage and fee, fill time = the candle's open) and a stop is checked against the mark;
end_candle(t) closes it (now_ms = t). advance_to never opens a candle, so the candle-close clock is unchanged.

Not in the port (STEP0 section 7 defers equity and income reads): `equity()` and `funding(...)` are the account reads
the S1 Runner needs; they are reported as an interface gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, Inexact, InvalidOperation, Overflow

from newcore.ports.bars import Bar
from newcore.ports.venue import (MarketOrder, OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, StopOrder,
                                 VenueFill, VenueOrder, VenuePosition)

ZERO = Decimal(0)
ONE = Decimal(1)
# Exact arithmetic: every venue product / sum is exact at this precision; anything inexact is a bug, not a rounding.
VCTX = Context(prec=60, traps=[InvalidOperation, Overflow, Inexact])
DCTX = Context(prec=34, traps=[InvalidOperation, Overflow])     # the one inexact step: a blended average entry price

E_REDUCE_ONLY = -2022
E_WOULD_TRIGGER = -2021
E_DUPLICATE_ID = -4116
E_NO_ORDER = -2013
E_UNKNOWN_ORDER = -2011
E_NO_MARKET = -1


@dataclass(frozen=True, slots=True)
class CostModel:
    taker_fee: Decimal = Decimal('0.0005')
    slip: Decimal = Decimal('0.0002')
    funding_per_bar: Decimal = ZERO


def path_points(o, h, l, c, position_side):
    """zb-path/1 for a position of `position_side` ('LONG' / 'SHORT')."""
    if c == o:
        return (o, h, l, c) if position_side == 'LONG' else (o, l, h, c)
    return (o, l, h, c) if c > o else (o, h, l, c)


@dataclass(slots=True)
class _Order:
    ref: OrderRef
    order_type: str              # MARKET | STOP_MARKET
    position_side: str
    qty: Decimal
    reduce: bool
    stop_price: Decimal | None
    exchange_order_id: str
    seq: int
    status: str = 'NEW'          # NEW | FILLED | CANCELED
    executed: Decimal = ZERO
    avg_price: Decimal | None = None


@dataclass(frozen=True, slots=True)
class FundingRow:
    symbol: str
    side: str
    at_ms: int
    amount: Decimal              # paid (> 0 = a cost)


def _buy(order_side, reduce):
    """Is the order a buy? Opening LONG / closing SHORT buy; opening SHORT / closing LONG sell."""
    return (order_side == 'LONG') != reduce


class FakeVenue:
    def __init__(self, candles, tf_ms, *, costs=CostModel(), equity=Decimal('10000'), start_ms=None, asset='USDT'):
        """candles: {symbol: tuple[Bar, ...]} oldest first, contiguous. start_ms: the venue clock at construction (a
        candle open; default the first candle's open)."""
        self.tf_ms = tf_ms
        self.costs = costs
        self.asset = asset
        self._bars = {s: tuple(b) for s, b in candles.items()}
        self._idx = {s: {b.open_ms: i for i, b in enumerate(bs)} for s, bs in self._bars.items()}
        first = min(bs[0].open_ms for bs in self._bars.values())
        self.now_ms = first if start_ms is None else start_ms
        if self.now_ms % tf_ms:
            raise ValueError('start_ms must be a candle boundary')
        self._wallet = equity
        self._positions = {}             # (symbol, side) -> [qty, avg]
        self._orders = {}                # client_id -> _Order (every order ever accepted)
        self._fills = []
        self._funding = []
        self._seq = 0
        self._trade_seq = 0
        # fault hooks
        self._lose_market = None
        self._not_found = {}
        self._unknown_reads = 0
        self._rest = 0                   # next n opening market orders rest unfilled (a working risk-adding order)
        self._fill_on_cancel = set()     # client ids whose remainder fills when the cancel arrives (cancel loses)
        self._lose_cancel = {}           # client id -> 'cancelled' | 'working': the cancel's answer is lost
        self._refuse_classic_stop = None  # error code: classic-route STOP_MARKET refused (the algo route is accepted)
        self._open = {}                  # intra-candle play: symbol -> the open candle (Bar)
        self._marks = {}                 # intra-candle play: symbol -> the current mark
        self.calls = {'submit_market': 0, 'submit_stop': 0, 'cancel': 0, 'query': 0, 'positions': 0, 'open_orders': 0,
                      'fills': 0}

    # ------------------------------------------------------------------------------------------------ fault hooks
    def lose_next_market_answer(self, truth='filled'):
        if truth not in ('filled', 'not_filled'):
            raise ValueError(truth)
        self._lose_market = truth

    def not_found(self, client_id, times=1):
        self._not_found[client_id] = self._not_found.get(client_id, 0) + times

    def unknown_reads(self, times=1):
        self._unknown_reads += times

    def rest_next_entries(self, n=1):
        """The next n opening market orders are accepted but rest unfilled (NEW): a working ENTRY / ADD."""
        self._rest += n

    def fill_resting(self, client_id, qty):
        """Execute `qty` of a resting order at the current candle's open (a partial or full fill)."""
        o = self._orders[client_id]
        if o.status != 'NEW' or o.order_type != 'MARKET':
            raise ValueError('not a resting market order')
        opn = self._next_open(o.ref.symbol)
        px = self._slipped(opn, buy=_buy(o.position_side, o.reduce))
        self._execute_part(o, qty, px, at_ms=self.now_ms)

    def fill_when_cancelled(self, client_id):
        """Race: when the cancel arrives, the remainder has just filled; the cancel answers 'not open'."""
        self._fill_on_cancel.add(client_id)

    def lose_next_cancel_answer(self, client_id, truth='cancelled'):
        """The next cancel of this order reaches the venue (truth 'cancelled': it is cancelled; 'working': it is not
        processed) but its answer is lost: UNKNOWN."""
        self._lose_cancel[client_id] = truth

    def refuse_classic_stops(self, code=-4120):
        """Classic /fapi/v1/order STOP_MARKET is refused with `code` (Binance -4120: use the algo service)."""
        self._refuse_classic_stop = code

    def external_cancel(self, client_id):
        """The order disappears outside the bot (an operator / the exchange cancelled it)."""
        o = self._orders[client_id]
        if o.status == 'NEW':
            o.status = 'CANCELED'

    def inject_position(self, symbol, side, qty, price):
        """A position the bot did not open (foreign / manual)."""
        self._apply_fill(symbol, side, qty, price, reduce=False, eoid='foreign', at_ms=self.now_ms, fee=ZERO)

    # ------------------------------------------------------------------------------------------------ clock
    def advance_to(self, t_ms):
        """Play every candle of every symbol with close_ms <= t_ms (stops, funding). Moves now_ms to t_ms."""
        if self._open:
            raise ValueError(f"advance_to({t_ms}): a candle is open (end_candle first)")
        if t_ms < self.now_ms or t_ms % self.tf_ms:
            raise ValueError(f'advance_to({t_ms}) from {self.now_ms}: only forward, to candle boundaries')
        t = self.now_ms
        while t + self.tf_ms <= t_ms:
            for s in sorted(self._bars):
                i = self._idx[s].get(t)
                if i is not None:
                    self._play(s, self._bars[s][i])
            t += self.tf_ms
        self.now_ms = t_ms

    def _play(self, symbol, bar):
        self._fund(symbol, bar)
        self._walk_stops(symbol, bar)

    def _fund(self, symbol, bar):
        fund = self.costs.funding_per_bar
        if fund:
            for (s, side), (q, _) in sorted(self._positions.items()):
                if s == symbol and q > 0:
                    amt = VCTX.multiply(VCTX.multiply(q, bar.close), fund)
                    self._wallet = VCTX.subtract(self._wallet, amt)
                    self._funding.append(FundingRow(s, side, bar.close_ms, amt))

    def _walk_stops(self, symbol, bar):
        stops = [o for o in self._orders.values() if o.status == 'NEW' and o.order_type == 'STOP_MARKET'
                 and o.ref.symbol == symbol]
        hits = []
        for o in stops:
            pts = path_points(bar.open, bar.high, bar.low, bar.close, o.position_side)
            through = (lambda px: px <= o.stop_price) if o.position_side == 'LONG' else (lambda px: px >= o.stop_price)
            for k, px in enumerate(pts):
                if through(px):
                    hits.append((k, o.seq, o, px if k == 0 else o.stop_price))
                    break
        for _, _, o, px in sorted(hits, key=lambda x: (x[0], x[1])):
            if o.status != 'NEW':
                continue
            pos = self._positions.get((symbol, o.position_side))
            if pos is None or pos[0] <= 0:
                o.status = 'CANCELED'                    # reduce-only with nothing to reduce: expires unfilled
                continue
            q = min(o.qty, pos[0])
            fill = self._slipped(px, buy=_buy(o.position_side, True))
            self._execute(o, q, fill, at_ms=bar.open_ms)

    # ------------------------------------------------------------------------------------------------ intra-candle
    def begin_candle(self, t_ms):
        """Open the candle that closes at t_ms (every symbol that has one): funding is charged, marks = the opens.
        Returns {symbol: Bar}."""
        if t_ms != self.now_ms + self.tf_ms or self._open:
            raise ValueError(f'begin_candle({t_ms}) at {self.now_ms}: one candle at a time, the next one')
        for s in sorted(self._bars):
            i = self._idx[s].get(self.now_ms)
            if i is not None:
                bar = self._bars[s][i]
                self._fund(s, bar)
                self._open[s] = bar
                self._marks[s] = bar.open
        return dict(self._open)

    def open_candle(self):
        """The candle being played ({symbol: Bar}; empty between candles): a restarted process resumes its walk."""
        return dict(self._open)

    def set_mark(self, symbol, price):
        if symbol not in self._open:
            raise ValueError(f'{symbol}: no open candle')
        self._marks[symbol] = price

    def stop_levels(self, symbol):
        """Resting STOP_MARKET orders of `symbol`: ((client_id, position_side, stop_price), ...) in placement order."""
        return tuple((o.ref.client_id, o.position_side, o.stop_price) for o in sorted(self._orders.values(),
                                                                                     key=lambda o: o.seq)
                     if o.status == 'NEW' and o.order_type == 'STOP_MARKET' and o.ref.symbol == symbol)

    def trigger_stop(self, client_id, px):
        """The mark reached a resting stop: it fills at `px` (the level, or the gapped point) with taker slippage; a
        reduce-only stop with nothing to reduce expires unfilled (as on a whole-candle walk)."""
        o = self._orders[client_id]
        bar = self._open[o.ref.symbol]
        pos = self._positions.get((o.ref.symbol, o.position_side))
        if pos is None or pos[0] <= 0:
            o.status = 'CANCELED'
            return
        q = min(o.qty, pos[0])
        self._execute(o, q, self._slipped(px, buy=_buy(o.position_side, True)), at_ms=bar.open_ms)

    def end_candle(self, t_ms):
        if not self._open or t_ms != self.now_ms + self.tf_ms:
            raise ValueError(f'end_candle({t_ms}) at {self.now_ms}: no such open candle')
        self._open, self._marks = {}, {}
        self.now_ms = t_ms

    # ------------------------------------------------------------------------------------------------ helpers
    def _slipped(self, px, *, buy):
        k = VCTX.add(ONE, self.costs.slip) if buy else VCTX.subtract(ONE, self.costs.slip)
        return VCTX.multiply(px, k)

    def _next_open(self, symbol):
        if symbol in self._open:                      # intra-candle: the current mark is the market
            return self._marks[symbol]
        i = self._idx.get(symbol, {}).get(self.now_ms)
        return None if i is None else self._bars[symbol][i].open

    def _new_order(self, ref, order_type, side, qty, reduce, stop_price):
        self._seq += 1
        o = _Order(ref=ref, order_type=order_type, position_side=side, qty=qty, reduce=reduce, stop_price=stop_price,
                   exchange_order_id=str(100000 + self._seq), seq=self._seq)
        self._orders[ref.client_id] = o
        return o

    def _apply_fill(self, symbol, side, q, px, *, reduce, eoid, at_ms, fee):
        key = (symbol, side)
        qty, avg = self._positions.get(key, (ZERO, ZERO))
        sign = ONE if side == 'LONG' else -ONE
        if reduce:
            realized = VCTX.multiply(VCTX.multiply(sign, VCTX.subtract(px, avg)), q)
            qty = VCTX.subtract(qty, q)
        else:
            realized = ZERO
            avg = DCTX.divide(VCTX.add(VCTX.multiply(avg, qty), VCTX.multiply(px, q)), VCTX.add(qty, q)) if qty else px
            qty = VCTX.add(qty, q)
        if qty > 0:
            self._positions[key] = [qty, avg]
        else:
            self._positions.pop(key, None)
        self._wallet = VCTX.subtract(VCTX.add(self._wallet, realized), fee)
        self._trade_seq += 1
        self._fills.append(VenueFill(trade_id=str(self._trade_seq), exchange_order_id=eoid, symbol=symbol,
                                     position_side=side, qty=q, price=px, fee=fee, fee_asset=self.asset,
                                     realized_pnl=realized, maker=False, at_ms=at_ms))

    def _execute(self, o, q, px, *, at_ms):
        fee = VCTX.multiply(VCTX.multiply(q, px), self.costs.taker_fee)
        self._apply_fill(o.ref.symbol, o.position_side, q, px, reduce=o.reduce, eoid=o.exchange_order_id, at_ms=at_ms,
                         fee=fee)
        o.executed, o.avg_price = q, px
        o.status = 'FILLED' if q == o.qty else 'CANCELED'

    def _execute_part(self, o, q, px, *, at_ms):
        """A fill of part (or the rest) of a resting order; it stays NEW until fully executed."""
        q = min(q, o.qty - o.executed)
        fee = VCTX.multiply(VCTX.multiply(q, px), self.costs.taker_fee)
        self._apply_fill(o.ref.symbol, o.position_side, q, px, reduce=o.reduce, eoid=o.exchange_order_id, at_ms=at_ms,
                         fee=fee)
        total = VCTX.add(o.executed, q)
        o.avg_price = px if not o.executed else DCTX.divide(
            VCTX.add(VCTX.multiply(o.avg_price, o.executed), VCTX.multiply(px, q)), total)
        o.executed = total
        if total == o.qty:
            o.status = 'FILLED'

    def _outcome(self, o):
        """The order's record as the venue reports it now."""
        if o.status == 'NEW':
            return OrderOutcome(kind=OutcomeKind.KNOWN, ref=o.ref, observed_at_ms=self.now_ms,
                                status='PARTIALLY_FILLED' if o.executed > 0 else 'NEW',
                                exchange_order_id=o.exchange_order_id)
        return OrderOutcome(kind=OutcomeKind.FINAL, ref=o.ref, observed_at_ms=self.now_ms, status=o.status,
                            exchange_order_id=o.exchange_order_id, executed_qty=o.executed,
                            avg_price=o.avg_price if o.executed > 0 else None)

    def _rejected(self, ref, code, detail):
        return OrderOutcome(kind=OutcomeKind.REJECTED, ref=ref, observed_at_ms=self.now_ms, error_code=code,
                            detail=detail)

    def _pos_qty(self, symbol, side):
        return self._positions.get((symbol, side), (ZERO, ZERO))[0]

    # ------------------------------------------------------------------------------------------------ VenuePort
    def submit_market(self, order: MarketOrder) -> OrderOutcome:
        self.calls['submit_market'] += 1
        ref = order.ref
        lose, self._lose_market = self._lose_market, None
        if ref.client_id in self._orders:
            return self._rejected(ref, E_DUPLICATE_ID, 'duplicate_client_id')
        if lose == 'not_filled':
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self.now_ms, detail='timeout')
        opn = self._next_open(ref.symbol)
        if opn is None:
            return self._rejected(ref, E_NO_MARKET, 'no_market')
        if order.reduce and order.qty > self._pos_qty(ref.symbol, order.position_side):
            return self._rejected(ref, E_REDUCE_ONLY, 'reduce_only')
        o = self._new_order(ref, 'MARKET', order.position_side, order.qty, order.reduce, None)
        if not order.reduce and self._rest > 0:                     # accepted, resting unfilled
            self._rest -= 1
            return self._outcome(o)
        self._execute(o, order.qty, self._slipped(opn, buy=_buy(order.position_side, order.reduce)), at_ms=self.now_ms)
        if lose == 'filled':
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self.now_ms, detail='timeout')
        return self._outcome(o)

    def submit_stop(self, order: StopOrder) -> OrderOutcome:
        self.calls['submit_stop'] += 1
        ref = order.ref
        if ref.client_id in self._orders:
            return self._rejected(ref, E_DUPLICATE_ID, 'duplicate_client_id')
        if ref.route == 'classic' and self._refuse_classic_stop is not None:
            return self._rejected(ref, self._refuse_classic_stop, 'algo_required')
        if order.qty > self._pos_qty(ref.symbol, order.position_side):
            return self._rejected(ref, E_REDUCE_ONLY, 'reduce_only')
        mark = self._next_open(ref.symbol)
        if mark is not None and ((mark <= order.stop_price) if order.position_side == 'LONG'
                                 else (mark >= order.stop_price)):
            return self._rejected(ref, E_WOULD_TRIGGER, 'would_trigger')
        o = self._new_order(ref, 'STOP_MARKET', order.position_side, order.qty, True, order.stop_price)
        return self._outcome(o)

    def cancel(self, ref: OrderRef) -> OrderOutcome:
        self.calls['cancel'] += 1
        o = self._orders.get(ref.client_id)
        if o is None or o.ref.symbol != ref.symbol:
            return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=self.now_ms,
                                error_code=E_UNKNOWN_ORDER)
        if o.status != 'NEW':
            return self._rejected(ref, E_UNKNOWN_ORDER, 'not_open')
        if ref.client_id in self._fill_on_cancel:                  # the fill wins the race
            self._fill_on_cancel.discard(ref.client_id)
            self.fill_resting(ref.client_id, o.qty - o.executed)
            return self._rejected(ref, E_UNKNOWN_ORDER, 'not_open')
        lost = self._lose_cancel.pop(ref.client_id, None)
        if lost != 'working':
            o.status = 'CANCELED'
        if lost is not None:
            return OrderOutcome(kind=OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=self.now_ms, detail='timeout')
        return self._outcome(o)

    def query(self, ref: OrderRef) -> OrderOutcome:
        self.calls['query'] += 1
        if self._not_found.get(ref.client_id, 0) > 0:
            self._not_found[ref.client_id] -= 1
            return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=self.now_ms, error_code=E_NO_ORDER)
        o = self._orders.get(ref.client_id)
        if o is None or o.ref.symbol != ref.symbol:
            return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=ref, observed_at_ms=self.now_ms, error_code=E_NO_ORDER)
        return self._outcome(o)

    def _read(self, name, value):
        self.calls[name] += 1
        if self._unknown_reads > 0:
            self._unknown_reads -= 1
            return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=self.now_ms, detail='timeout')
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=self.now_ms, value=value)

    def positions(self, symbol=None):
        syms = sorted({s for s in self._bars} | {s for s, _ in self._positions})
        out = []
        for s in syms:
            if symbol is not None and s != symbol:
                continue
            for side in ('LONG', 'SHORT'):
                q, avg = self._positions.get((s, side), (ZERO, ZERO))
                out.append(VenuePosition(symbol=s, side=side, qty=q, entry_price=avg))
        return self._read('positions', tuple(out))

    def open_orders(self, symbol=None):
        out = tuple(VenueOrder(ref=o.ref, exchange_order_id=o.exchange_order_id, position_side=o.position_side,
                               reduce=o.reduce, order_type=o.order_type, status=o.status, qty=o.qty,
                               close_position=False, stop_price=o.stop_price)
                    for o in sorted(self._orders.values(), key=lambda o: o.seq)
                    if o.status == 'NEW' and (symbol is None or o.ref.symbol == symbol))
        return self._read('open_orders', out)

    def fills(self, symbol, exchange_order_id):
        self.calls['fills'] += 1
        out = tuple(f for f in self._fills if f.symbol == symbol and f.exchange_order_id == exchange_order_id)
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=self.now_ms, value=out)

    # ------------------------------------------------------------------------------------------------ account reads
    def equity(self) -> ReadOutcome:
        """Wallet balance (start + realized - fees - funding): the 'closed equity' the sizing rule risks 1% of."""
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=self.now_ms, value=(self._wallet,))

    def funding(self, symbol, side, from_ms, to_ms) -> ReadOutcome:
        """Funding paid by (symbol, side) for candles closing in (from_ms, to_ms]: tuple of FundingRow."""
        out = tuple(r for r in self._funding if r.symbol == symbol and r.side == side and from_ms < r.at_ms <= to_ms)
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=self.now_ms, value=out)

    # ------------------------------------------------------------------------------------------------ persistence
    def to_state(self) -> dict:
        """Everything the venue holds besides its candles, as JSON-able data (Decimals as text), so a `fake` run can
        stop and restart next to its journal. Fault hooks are not state (they are test instruments)."""
        s = str
        return {
            'schema': 'zackbot.newcore.fake_venue/1', 'now_ms': self.now_ms, 'wallet': s(self._wallet),
            'seq': self._seq, 'trade_seq': self._trade_seq,
            'positions': [[sym, side, s(q), s(a)] for (sym, side), (q, a) in sorted(self._positions.items())],
            'orders': [{'symbol': o.ref.symbol, 'client_id': o.ref.client_id, 'route': o.ref.route, 'type': o.order_type,
                        'side': o.position_side, 'qty': s(o.qty), 'reduce': o.reduce,
                        'stop': None if o.stop_price is None else s(o.stop_price), 'eoid': o.exchange_order_id,
                        'seq': o.seq, 'status': o.status, 'executed': s(o.executed),
                        'avg': None if o.avg_price is None else s(o.avg_price)}
                       for o in sorted(self._orders.values(), key=lambda o: o.seq)],
            'fills': [[f.trade_id, f.exchange_order_id, f.symbol, f.position_side, s(f.qty), s(f.price), s(f.fee),
                       f.fee_asset, s(f.realized_pnl), f.maker, f.at_ms] for f in self._fills],
            'funding': [[r.symbol, r.side, r.at_ms, s(r.amount)] for r in self._funding],
        }

    @classmethod
    def from_state(cls, candles, tf_ms, state, *, costs=CostModel()):
        if state.get('schema') != 'zackbot.newcore.fake_venue/1':
            raise ValueError('not a fake venue state')
        D = Decimal
        v = cls(candles, tf_ms, costs=costs, equity=D(state['wallet']), start_ms=state['now_ms'])
        v._seq, v._trade_seq = state['seq'], state['trade_seq']
        v._positions = {(sym, side): [D(q), D(a)] for sym, side, q, a in state['positions']}
        for o in state['orders']:
            ref = OrderRef(symbol=o['symbol'], client_id=o['client_id'], route=o['route'])
            v._orders[o['client_id']] = _Order(
                ref=ref, order_type=o['type'], position_side=o['side'], qty=D(o['qty']), reduce=o['reduce'],
                stop_price=None if o['stop'] is None else D(o['stop']), exchange_order_id=o['eoid'], seq=o['seq'],
                status=o['status'], executed=D(o['executed']), avg_price=None if o['avg'] is None else D(o['avg']))
        v._fills = [VenueFill(trade_id=t, exchange_order_id=e, symbol=sym, position_side=side, qty=D(q), price=D(p),
                              fee=D(fee), fee_asset=asset, realized_pnl=D(r), maker=m, at_ms=at)
                    for t, e, sym, side, q, p, fee, asset, r, m, at in state['fills']]
        v._funding = [FundingRow(sym, side, at, D(a)) for sym, side, at, a in state['funding']]
        return v

    # ------------------------------------------------------------------------------------------------ inspection
    def orders_submitted(self, symbol=None):
        """Every order the venue accepted, oldest first (duplicate checks in tests)."""
        return tuple(o for o in sorted(self._orders.values(), key=lambda o: o.seq)
                     if symbol is None or o.ref.symbol == symbol)

    def bar(self, symbol, open_ms) -> Bar | None:
        i = self._idx.get(symbol, {}).get(open_ms)
        return None if i is None else self._bars[symbol][i]
