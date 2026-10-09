"""TestnetVenue: the frozen newcore.ports.VenuePort over BinanceTestnetTransport (STEP0_INTERFACE.md section 4).

One port call = one transport call = at most one venue request (open_orders: one classic + one algo read). Nothing is
retried and no route is switched here: the Runner journals each route as its own attempt intent (a classic -> algo
fallback is a NEW protect intent with client_id_for(intent_id, 'algo')). The client id is the one the Runner derived
with newcore.ports.keys.client_id_for(intent_id, route); the adapter sends it verbatim and refuses (PortValueError,
nothing sent) a submit / cancel whose ref is not a NEWCORE id of the ref's own route.

Outcome mapping (map_order_outcome, pure; conflict C4): transport kinds map one to one, except
  - only FINAL carries executed_qty (and avg_price exactly when something executed);
  - NOT_FOUND keeps its evidence: error_code = the venue code, detail = the evidence type (never a fill);
  - a duplicate-client-id refusal means the order EXISTS: the transport already returns UNKNOWN
    'duplicate_client_id' (query by client id), never REJECTED-nothing-executed;
  - an algo conditional that FINISHED / triggered carries no executed quantity of its own: it maps to KNOWN with the
    child order id as exchange_order_id and detail 'algo_triggered' (fill truth = fills(child id)); an algo order
    CANCELED / EXPIRED / REJECTED without a child order is FINAL with 0 executed.

Hedge mode is required: the transport must be built with PositionMode.HEDGE, and check_hedge_mode() (boot) refuses an
account in one-way mode (HedgeModeRequired) or an unreadable mode (VenueBootUnknown).

Equity and funding are NOT on the port (Codex): TestnetAccountReader reads them explicitly for the Runner.

fills(symbol, start_ms=, end_ms=) (REC-02): every own trade of the symbol in [start_ms, end_ms] as TradeFill (side
BUY / SELL, positionSide, ids, qty, price, commission + asset, realizedPnl, time), oldest first. Paged over
/fapi/v1/userTrades: 7-day windows, inside a window a full page continues with fromId (Binance refuses fromId with a
time window); at most FILL_WINDOW_PAGES pages in all. Complete or not OK: any page that is not OK, a conflicting
duplicate in a page (malformed), an out-of-order page or the page bound -> UNKNOWN (REJECTED passed through),
never a partial list as OK.
fills(symbol, exchange_order_id) is unchanged (the port read).

order_by_id(symbol, exchange_order_id) (S1 / Codex 6071659449): GET /fapi/v1/order?symbol=&orderId= first (trade rows
carry this id; a triggered algo stop's child is a classic order). Only when that answers -2013 is the id tried as an
algo id (GET /fapi/v1/algoOrder?algoId=; open_orders and FINAL algo outcomes hand out algo ids as exchange ids) - so a
NOT_FOUND costs two requests. OK = (VenueOrderRecord,) of exactly the asked id and symbol; REJECTED -2013 = neither
endpoint holds it; UNKNOWN for no answer, malformed JSON, a missing field, a JSON-number (float-looking) quantity, an
id / symbol echo mismatch, a one-way (BOTH) order, or a TRIGGERED algo order (its executed quantity lives in the
child order: look that id up). Route of a classic record: 'algo' when its client id is a NEWCORE algo id, else
'classic'.

TestnetAccountReader.mark_price(symbol): /fapi/v1/premiumIndex -> (MarkQuote(symbol, price, at_ms = Binance server
time),); one request, never retried; OK / REJECTED / UNKNOWN. WEIGHT: premiumIndex with a symbol costs 1 weight per
call, so a poll every cycle.mark_poll_s seconds over N symbols is N x 60 / mark_poll_s weight per minute (10 s x 8
symbols = 48 / min) on top of the cycle's reads; keep poll cadence x symbols well under the 2400 / min IP limit
(the transport reports X-MBX-USED-WEIGHT-1M on every answer).
"""
from dataclasses import dataclass
from decimal import Decimal

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError, check_symbol, req

from . import records as R
from .errors import ErrorCategory
from .income import FUNDING_FEE, income_history
from .outcomes import OrderOutcomeKind as TK
from .outcomes import ReadKind as TR
from .transport import _NOT_FOUND_TEXT, CLOSING_SIDE, OPENING_SIDE, PositionMode, StopRoute

ROUTE_CHAR = {'classic': 'zbn1o-', 'algo': 'zbn1a-'}
E_NO_ORDER = -2013                           # Binance 'Order does not exist.'
USER_TRADES_LIMIT = 1000
FILL_WINDOW_MS = 7 * 24 * 3600 * 1000 - 1    # one userTrades time window (Binance: at most 7 days)
FILL_WINDOW_PAGES = 20                       # all pages of one fills(start, end) read
TRADES_MAX_PAGES = 64                        # all pages of one trades(symbol, side, from_ms) read: ~48
                                             # 7-day windows = S1's 2000-candle 4h search, + full pages
MARK_MAX_AGE_MS = 30_000                     # a mark older than this (server clock) is stale
MARK_MAX_AHEAD_MS = 5_000                    # a mark further ahead of the server clock is not believed


def _raw(rows):
    """The rows Binance SENT on this page (a deduped repeat still counts): a page is FULL by this, never by len()."""
    return getattr(rows, 'raw', len(rows))


def evidence(pages, dups):
    """The completeness evidence on an OK fills read (ReadOutcome.detail, <= 32 chars): OK = every page was read to a
    raw-SHORT page, time never going back (Codex 6069281718); 'dups' = repeated rows dropped by trade id (Cowork
    6068372233)."""
    return f'complete pages={pages} dups={dups}'


class HedgeModeRequired(Exception):
    """NEWCORE requires hedge mode (dualSidePosition=true); the transport or the account is in one-way mode."""


class VenueBootUnknown(Exception):
    """A boot check could not read the account state; unknown is never assumed to be fine."""


# ---------------------------------------------------------------------------------------------- pure mappings
def _code(err):
    if err is None:
        return None
    if isinstance(err.code, int) and not isinstance(err.code, bool):
        return err.code
    return -int(err.http_status) if isinstance(err.http_status, int) else -1


def map_order_outcome(t, ref, observed_at_ms):
    """Transport OrderOutcome -> port OrderOutcome (pure)."""
    base = dict(ref=ref, observed_at_ms=observed_at_ms)
    k = t.kind
    if k is TK.UNKNOWN:
        return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, detail=t.unknown_reason or 'unknown',
                              error_code=_code(t.error) if t.error is not None else None, **base)
    if k is TK.ACKNOWLEDGED:
        return P.OrderOutcome(kind=P.OutcomeKind.ACKNOWLEDGED, **base)
    if k is TK.REJECTED:
        e = t.error
        detail = 'algo_route' if e.suggests_algo_route else e.category.value
        return P.OrderOutcome(kind=P.OutcomeKind.REJECTED, error_code=_code(e), detail=detail, **base)
    if k is TK.NOT_FOUND:
        return P.OrderOutcome(kind=P.OutcomeKind.NOT_FOUND, error_code=_code(t.error),
                              detail=t.not_found.evidence_type.value, **base)
    rec = t.record
    if isinstance(rec, R.AlgoOrderRecord):
        status = rec.algo_status
        if rec.actual_order_id is not None or status in ('TRIGGERING', 'TRIGGERED', 'FINISHED'):
            if rec.actual_order_id is None:
                return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, detail='algo_triggered_no_child', **base)
            return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, status=status, exchange_order_id=rec.actual_order_id,
                                  detail='algo_triggered', **base)
        if rec.is_final:
            return P.OrderOutcome(kind=P.OutcomeKind.FINAL, status=status, exchange_order_id=str(rec.algo_id),
                                  executed_qty=Decimal('0'), **base)
        return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, status=status, exchange_order_id=str(rec.algo_id), **base)
    if k is TK.KNOWN:
        return P.OrderOutcome(kind=P.OutcomeKind.KNOWN, status=rec.status, exchange_order_id=str(rec.order_id), **base)
    # FINAL classic record: the only executed quantity the ledger may book
    executed = rec.executed_qty
    avg = rec.avg_price if executed > 0 else None
    if executed > 0 and (avg is None or avg <= 0):
        return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, detail='final_without_price', **base)
    return P.OrderOutcome(kind=P.OutcomeKind.FINAL, status=rec.status, exchange_order_id=str(rec.order_id),
                          executed_qty=executed, avg_price=avg, **base)


def _safe_map(t, ref, observed_at_ms):
    """The port never raises for a venue answer: an answer the port values cannot represent is UNKNOWN."""
    try:
        return map_order_outcome(t, ref, observed_at_ms)
    except (PortValueError, ValueError, TypeError):
        return P.OrderOutcome(kind=P.OutcomeKind.UNKNOWN, ref=ref, observed_at_ms=observed_at_ms,
                              detail='unrepresentable')


def map_read_outcome(t, observed_at_ms, convert):
    """Transport ReadOutcome -> port ReadOutcome; convert(value) -> tuple or raises (-> UNKNOWN 'malformed')."""
    if t.kind is TR.OK:
        try:
            value = convert(t.value)
        except (PortValueError, ValueError, TypeError, KeyError):
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=observed_at_ms, detail='unrepresentable')
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=observed_at_ms, value=value)
    if t.kind is TR.REJECTED:
        return P.ReadOutcome(kind=P.ReadKind.REJECTED, observed_at_ms=observed_at_ms, error_code=_code(t.error),
                             detail=t.error.category.value)
    return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=observed_at_ms, detail=t.unknown_reason or 'unknown')


# ---------------------------------------------------------------------------------------------- value conversion
def _position(row):
    req(row.position_side in ('LONG', 'SHORT'), 'positionRisk.positionSide', 'LONG / SHORT (hedge mode required)')
    return P.VenuePosition(symbol=row.symbol, side=row.position_side, qty=abs(row.position_amt),
                           entry_price=row.entry_price)


def _reducing(position_side, side, reduce_only, close_position):
    return bool(reduce_only or close_position or side == CLOSING_SIDE.get(position_side))


def _classic_order(o):
    req(o.position_side in ('LONG', 'SHORT'), 'openOrders.positionSide', 'LONG / SHORT (hedge mode required)')
    stop = o.stop_price if o.stop_price is not None and o.stop_price > 0 else None
    return P.VenueOrder(ref=P.OrderRef(symbol=o.symbol, client_id=o.client_order_id, route='classic'),
                        exchange_order_id=str(o.order_id), position_side=o.position_side,
                        reduce=_reducing(o.position_side, o.side, o.reduce_only, o.close_position),
                        order_type=o.type, status=o.status, qty=o.orig_qty - o.executed_qty,
                        close_position=o.close_position, stop_price=stop)


def _algo_order(a):
    req(a.position_side in ('LONG', 'SHORT'), 'openAlgoOrders.positionSide', 'LONG / SHORT (hedge mode required)')
    return P.VenueOrder(ref=P.OrderRef(symbol=a.symbol, client_id=a.client_algo_id, route='algo'),
                        exchange_order_id=str(a.algo_id), position_side=a.position_side,
                        reduce=_reducing(a.position_side, a.side, a.reduce_only, a.close_position),
                        order_type=a.order_type, status=a.algo_status, qty=a.quantity,
                        close_position=a.close_position, stop_price=a.trigger_price)


def _fill(f):
    return P.VenueFill(trade_id=str(f.trade_id), exchange_order_id=str(f.order_id), symbol=f.symbol,
                       position_side=f.position_side, qty=f.qty, price=f.price, fee=f.commission,
                       fee_asset=f.commission_asset, realized_pnl=f.realized_pnl, maker=f.maker, at_ms=f.time_ms)


# ---------------------------------------------------------------------------------------------- the adapter
class TestnetVenue:
    __test__ = False

    def __init__(self, transport, clock):
        if getattr(transport, 'position_mode', None) is not PositionMode.HEDGE:
            raise HedgeModeRequired('the transport must be built with PositionMode.HEDGE')
        if not callable(clock):
            raise PortValueError('clock', 'callable() -> int ms')
        self._t, self._clock = transport, clock

    def __repr__(self):
        return f'TestnetVenue({self._t!r})'

    @property
    def transport(self):
        """The underlying transport (read access for harnesses such as the cassette replay)."""
        return self._t

    def _now(self):
        return self._clock()

    def check_hedge_mode(self):
        """Boot check: the ACCOUNT must be in hedge mode. Raises HedgeModeRequired / VenueBootUnknown."""
        r = self._t.dual_side_position()
        if r.kind is not TR.OK:
            raise VenueBootUnknown(f'position mode could not be read ({r.kind.value})')
        if r.value is not True:
            raise HedgeModeRequired('the testnet account is in one-way mode; NEWCORE requires hedge mode')

    @staticmethod
    def _owned(ref, route=None):
        req(isinstance(ref, P.OrderRef), 'ref', 'an OrderRef')
        req(K.is_newcore_client_id(ref.client_id) and ref.client_id.startswith(ROUTE_CHAR[ref.route]),
            'ref.client_id', "a NEWCORE client id of the ref's own route (keys.client_id_for(intent_id, route))")
        if route is not None:
            req(ref.route == route, 'ref.route', route)

    # ---- orders
    def submit_market(self, order):
        req(isinstance(order, P.MarketOrder), 'order', 'a MarketOrder')
        self._owned(order.ref, 'classic')
        ps = order.position_side
        side = CLOSING_SIDE[ps] if order.reduce else OPENING_SIDE[ps]
        t = self._t.place_market(order.ref.symbol, side, ps, order.qty, order.ref.client_id, reduce_only=order.reduce)
        return _safe_map(t, order.ref, self._now())

    def submit_stop(self, order):
        req(isinstance(order, P.StopOrder), 'order', 'a StopOrder')
        self._owned(order.ref)
        t = self._t.place_stop_market(order.ref.symbol, order.position_side, order.qty, order.stop_price,
                                      order.ref.client_id, route=StopRoute(order.ref.route))
        return _safe_map(t, order.ref, self._now())

    def cancel(self, ref):
        self._owned(ref)
        t = (self._t.cancel_algo_order(ref.client_id) if ref.route == 'algo'
             else self._t.cancel_order(ref.symbol, ref.client_id))
        return _safe_map(t, ref, self._now())

    def query(self, ref):
        req(isinstance(ref, P.OrderRef), 'ref', 'an OrderRef')
        t = (self._t.query_algo_order(ref.client_id) if ref.route == 'algo'
             else self._t.query_order(ref.symbol, ref.client_id))
        return _safe_map(t, ref, self._now())

    # ---- reads
    def positions(self, symbol=None):
        t = self._t.positions(symbol)
        return map_read_outcome(t, self._now(), lambda rows: tuple(_position(r) for r in rows))

    def open_orders(self, symbol=None):
        classic = self._t.open_orders(symbol)
        if classic.kind is not TR.OK:
            return map_read_outcome(classic, self._now(), tuple)
        algo = self._t.open_algo_orders(symbol)
        if algo.kind is not TR.OK:                     # a partial read is never OK: unknown algo stops != none
            if algo.kind is TR.REJECTED:
                return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(),
                                     detail='algo_read_' + algo.error.category.value[:20])
            return map_read_outcome(algo, self._now(), tuple)
        return map_read_outcome(classic, self._now(),
                                lambda rows: tuple(_classic_order(o) for o in rows)
                                + tuple(_algo_order(a) for a in algo.value))

    def fills(self, symbol, exchange_order_id=None, *, start_ms=None, end_ms=None):
        if exchange_order_id is None:
            return self._fills_window(symbol, start_ms, end_ms)
        req(start_ms is None and end_ms is None, 'fills', 'by order OR by time window, not both')
        req(isinstance(exchange_order_id, str) and exchange_order_id.isdigit() and exchange_order_id.isascii()
            and not exchange_order_id.startswith('0'), 'exchange_order_id', 'a positive decimal order id')
        t = self._t.user_trades(symbol, order_id=int(exchange_order_id), limit=USER_TRADES_LIMIT)
        if t.kind is TR.OK and _raw(t.value) >= USER_TRADES_LIMIT:     # FULL (raw rows): more may exist
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='fills_truncated')
        dups = getattr(t.value, 'duplicates', 0) if t.kind is TR.OK else 0

        def convert(rows):
            if any(r.order_id != int(exchange_order_id) or r.symbol != symbol for r in rows):
                raise ValueError('fill of another order')
            return tuple(_fill(f) for f in sorted(rows, key=lambda f: (f.time_ms, f.trade_id)))
        out = map_read_outcome(t, self._now(), convert)
        if out.kind is P.ReadKind.OK:                                  # OK = complete: a short page
            out = P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=out.observed_at_ms, value=out.value,
                                detail=evidence(1, dups))
        return out


    def order_by_id(self, symbol, exchange_order_id):
        check_symbol(symbol, 'order_by_id.symbol')
        req(isinstance(exchange_order_id, str) and exchange_order_id.isdigit() and exchange_order_id.isascii()
            and not exchange_order_id.startswith('0') and len(exchange_order_id) <= 19, 'exchange_order_id',
            'a positive decimal order id')
        oid = int(exchange_order_id)
        t = self._t.query_order_by_id(symbol, oid)
        if t.kind is TR.REJECTED and _code(t.error) == E_NO_ORDER:
            return self._algo_by_id(symbol, oid)
        if t.kind is not TR.OK:
            return map_read_outcome(t, self._now(), tuple)
        rec = t.value
        if rec.order_id != oid or rec.symbol != symbol:
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='echo_mismatch')
        route = 'algo' if rec.client_order_id.startswith(ROUTE_CHAR['algo']) else 'classic'
        return map_read_outcome(t, self._now(), lambda r: (P.VenueOrderRecord(
            ref=P.OrderRef(symbol=r.symbol, client_id=r.client_order_id, route=route),
            exchange_order_id=str(r.order_id), position_side=r.position_side, status=r.status,
            orig_qty=r.orig_qty, executed_qty=r.executed_qty),))

    def _algo_by_id(self, symbol, algo_id):
        a = self._t.query_algo_order_by_id(algo_id)
        now = self._now()
        if a.kind is TR.REJECTED:
            e = a.error
            if _code(e) == E_NO_ORDER or any(x in (e.msg or '').lower() for x in _NOT_FOUND_TEXT):
                return P.ReadOutcome(kind=P.ReadKind.REJECTED, observed_at_ms=now, error_code=E_NO_ORDER,
                                     detail='not_found')
            # the classic book said "no such order" but the algo book could not answer: never NOT_FOUND
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now,
                                 detail='algo_read_' + e.category.value[:20])
        if a.kind is not TR.OK:
            return map_read_outcome(a, now, tuple)
        rec = a.value
        if rec.algo_id != algo_id or rec.symbol != symbol:
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now, detail='echo_mismatch')
        if rec.actual_order_id is not None or rec.algo_status in ('TRIGGERING', 'TRIGGERED', 'FINISHED'):
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now, detail='algo_triggered')
        return map_read_outcome(a, now, lambda r: (P.VenueOrderRecord(
            ref=P.OrderRef(symbol=r.symbol, client_id=r.client_algo_id, route='algo'),
            exchange_order_id=str(r.algo_id), position_side=r.position_side, status=r.algo_status,
            orig_qty=r.quantity, executed_qty=Decimal('0')),))

    def _fills_window(self, symbol, start_ms, end_ms):
        req(type(start_ms) is int and type(end_ms) is int and 0 < start_ms <= end_ms, 'fills',
            'start_ms <= end_ms, both int ms')
        got = self._window_rows(symbol, start_ms, end_ms, FILL_WINDOW_PAGES)
        if isinstance(got, P.ReadOutcome):
            return got
        rows, pages, dups = got
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=self._now(), value=tuple(_trade_fill(f) for f in rows),
                             detail=evidence(pages, dups))

    def trades(self, symbol, side, from_ms):
        """userTrades of one (symbol, position side) since from_ms (inclusive) up to the venue clock now: port VenueFill
        rows, oldest first - the read S1's ownership / provenance checks use (FakeVenue.trades has the same shape).
        OK = complete (every 7-day window read to a raw-SHORT page, never to a timestamp; repeated rows deduped by
        trade id and counted in detail); a conflicting repeat, an unseen older row, time going back within or across
        continuation pages, a one-way (BOTH) row, an error on any page or more than TRADES_MAX_PAGES pages ->
        UNKNOWN, never a partial list. WEIGHT: userTrades
        costs 5 per request; a deep search (from_ms a year back) is up to ~48 window requests (boot only, S1)."""
        req(side in ('LONG', 'SHORT'), 'trades.side', 'LONG or SHORT (a hedge position side)')
        req(type(from_ms) is int and from_ms > 0, 'trades.from_ms', 'a positive int ms')
        now = self._now()
        if from_ms > now:
            return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=now, value=(), detail=evidence(0, 0))
        got = self._window_rows(symbol, from_ms, now, TRADES_MAX_PAGES)
        if isinstance(got, P.ReadOutcome):
            return got
        rows, pages, dups = got
        if any(r.position_side not in ('LONG', 'SHORT') for r in rows):
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='one_way_row')
        mine = tuple(_fill(r) for r in rows if r.position_side == side)
        return P.ReadOutcome(kind=P.ReadKind.OK, observed_at_ms=self._now(), value=mine, detail=evidence(pages, dups))

    def _window_rows(self, symbol, start_ms, end_ms, max_pages):
        """Every userTrades row of `symbol` with start_ms <= time <= end_ms, walked in 7-day windows (Binance's span
        limit) and fromId pages: -> (rows oldest first, pages read, repeated rows dropped), or a typed ReadOutcome
        (REJECTED / UNKNOWN as the page answered; UNKNOWN out_of_order / time_regression / conflicting_trade /
        paging_bound). A page is FULL by the RAW row count Binance sent (a dedupe never makes it look short). A window
        is complete ONLY at a raw-SHORT page (Codex 6069281718): a row past the window end is never taken as proof,
        since nothing makes time monotonic with the trade id - and that order is checked: trade ids ascending, time
        never going back within a page or from one continuation page to the next (else UNKNOWN time_regression).
        The FIRST page of a window is bounded (startTime / endTime): a row outside them is UNKNOWN window_mismatch,
        never filtered (Codex 6069415268); fromId pages cannot be bounded, so a row past the end is valid there, kept
        as seen, and EMITTED when its own window returns it (Cowork 6069451524 - not dropped as an overlap, not a
        dup). Every row is emitted in exactly the window that contains it."""
        out, seen, pages, dups, w0 = {}, {}, 0, 0, start_ms
        while w0 <= end_ms:
            w1 = min(end_ms, w0 + FILL_WINDOW_MS)
            from_id, prev = None, None                       # prev: the last row of the previous page of this window
            while True:
                pages += 1
                if pages > max_pages:
                    return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='paging_bound')
                if from_id is None:
                    t = self._t.user_trades(symbol, start_ms=w0, end_ms=w1, limit=USER_TRADES_LIMIT)
                else:
                    t = self._t.user_trades(symbol, from_id=from_id, limit=USER_TRADES_LIMIT)
                if t.kind is not TR.OK:
                    return map_read_outcome(t, self._now(), lambda v: ())      # REJECTED / UNKNOWN as is
                rows = list(t.value)
                dups += getattr(t.value, 'duplicates', 0)           # repeated rows inside the page (deduped)
                if any(r.symbol != symbol for r in rows) or rows != sorted(rows, key=lambda r: (r.trade_id,)):
                    return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='out_of_order')
                chain = ([prev] if prev is not None else []) + rows   # this page, joined to the previous one
                if any(b.time_ms < a.time_ms for a, b in zip(chain, chain[1:])):
                    return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='time_regression')
                if from_id is None and any(not w0 <= r.time_ms <= w1 for r in rows):
                    return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(),   # Codex 6069415268:
                                         detail='window_mismatch')     # a BOUNDED page answered outside its bounds
                for r in rows:                                   # (fromId pages cannot be bounded: past-end is valid)
                    if r.trade_id in seen:                       # an overlap with an earlier page
                        if seen[r.trade_id] != r:
                            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(),
                                                 detail='conflicting_trade')
                        if w0 <= r.time_ms <= w1 and r.trade_id not in out:
                            out[r.trade_id] = r                  # Cowork 6069451524: first SEEN past an earlier
                            continue                             # window's end (fromId), emitted in its own window
                        dups += 1                                # a true repeat of an emitted / out-of-window row
                        continue
                    if from_id is not None and r.trade_id < from_id:   # older than asked and never seen
                        return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(),
                                             detail='out_of_order')
                    seen[r.trade_id] = r
                    if w0 <= r.time_ms <= w1:
                        out[r.trade_id] = r
                if _raw(t.value) < USER_TRADES_LIMIT:
                    break                                         # complete: a raw-SHORT page, never a timestamp
                prev = rows[-1]
                from_id = rows[-1].trade_id + 1
            w0 = w1 + 1
        return sorted(out.values(), key=lambda f: (f.time_ms, f.trade_id)), pages, dups


@dataclass(frozen=True)
class TradeFill:
    """One own trade with its side (REC-02: a manual close / late fill is attributed by side + positionSide)."""
    trade_id: str
    exchange_order_id: str
    symbol: str
    side: str                    # BUY / SELL
    position_side: str           # LONG / SHORT (BOTH only on a one-way account)
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    realized_pnl: Decimal
    maker: bool
    at_ms: int


def _trade_fill(f):
    return TradeFill(trade_id=str(f.trade_id), exchange_order_id=str(f.order_id), symbol=f.symbol, side=f.side,
                     position_side=f.position_side, qty=f.qty, price=f.price, fee=f.commission,
                     fee_asset=f.commission_asset, realized_pnl=f.realized_pnl, maker=f.maker, at_ms=f.time_ms)


@dataclass(frozen=True)
class MarkQuote:
    symbol: str
    price: Decimal
    at_ms: int                   # Binance server time of the mark


# ---------------------------------------------------------------------------------------------- equity / funding
@dataclass(frozen=True)
class Equity:
    asset: str
    wallet_balance: Decimal
    margin_balance: Decimal
    available_balance: Decimal
    unrealized_pnl: Decimal


@dataclass(frozen=True)
class FundingPayment:
    symbol: object               # str, or None for an account-level row
    asset: str
    amount: Decimal              # signed: negative = paid
    at_ms: int
    tran_id: int


class TestnetAccountReader:
    """Equity and funding reads for the Runner (deliberately NOT on VenuePort). Same typed ReadOutcome: OK (tuple) /
    REJECTED / UNKNOWN, never a partial or empty-on-failure answer."""
    __test__ = False

    def __init__(self, transport, clock):
        if not callable(clock):
            raise PortValueError('clock', 'callable() -> int ms')
        self._t, self._clock = transport, clock

    def equity(self, asset='USDT'):
        """value: (Equity,) for the asset; an account without that asset is UNKNOWN 'asset_missing', never zero."""
        t = self._t.account()

        def convert(acct):
            rows = [a for a in acct.assets if a.asset == asset]
            if len(rows) != 1:
                raise ValueError('asset missing')
            a = rows[0]
            return (Equity(asset, a.wallet_balance, a.margin_balance, a.available_balance, a.unrealized_profit),)
        out = map_read_outcome(t, self._clock(), convert)
        if out.kind is P.ReadKind.UNKNOWN and t.kind is TR.OK:
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=out.observed_at_ms, detail='asset_missing')
        return out

    def mark_price(self, symbol, *, max_age_ms=MARK_MAX_AGE_MS, max_ahead_ms=MARK_MAX_AHEAD_MS):
        """value: (MarkQuote,) from /fapi/v1/premiumIndex. One request, never retried; a mark of another symbol is
        UNKNOWN 'unrepresentable'; a mark older than max_age_ms or more than max_ahead_ms ahead of the (server-
        aligned) clock is UNKNOWN 'stale_mark' / 'future_mark' (M1)."""
        t = self._t.mark_price(symbol)

        def convert(m):
            if m.symbol != symbol:
                raise ValueError('mark of another symbol')
            return (MarkQuote(m.symbol, m.mark_price, m.time_ms),)
        now = self._clock()
        out = map_read_outcome(t, now, convert)
        if out.kind is P.ReadKind.OK:
            at = out.value[0].at_ms
            if at < now - max_age_ms:
                return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now, detail='stale_mark')
            if at > now + max_ahead_ms:
                return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=now, detail='future_mark')
        return out

    def funding(self, *, start_ms, end_ms, symbol=None):
        """value: tuple[FundingPayment, ...] for FUNDING_FEE rows in [start_ms, end_ms], oldest first (complete or not
        OK: income_history never returns a partial history)."""
        t = income_history(self._t, start_ms=start_ms, end_ms=end_ms, symbol=symbol, income_type=FUNDING_FEE)
        return map_read_outcome(t, self._clock(), lambda rows: tuple(
            FundingPayment(r.symbol, r.asset, r.income, r.time_ms, r.tran_id) for r in rows))
