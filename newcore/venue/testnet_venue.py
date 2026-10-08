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
"""
from dataclasses import dataclass
from decimal import Decimal

from newcore.ports import keys as K
from newcore.ports import venue as P
from newcore.ports.values import PortValueError, req

from . import records as R
from .errors import ErrorCategory
from .income import FUNDING_FEE, income_history
from .outcomes import OrderOutcomeKind as TK
from .outcomes import ReadKind as TR
from .transport import CLOSING_SIDE, OPENING_SIDE, PositionMode, StopRoute

ROUTE_CHAR = {'classic': 'zbn1o-', 'algo': 'zbn1a-'}
USER_TRADES_LIMIT = 1000


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

    def fills(self, symbol, exchange_order_id):
        req(isinstance(exchange_order_id, str) and exchange_order_id.isdigit() and exchange_order_id.isascii()
            and not exchange_order_id.startswith('0'), 'exchange_order_id', 'a positive decimal order id')
        t = self._t.user_trades(symbol, order_id=int(exchange_order_id), limit=USER_TRADES_LIMIT)
        if t.kind is TR.OK and len(t.value) >= USER_TRADES_LIMIT:
            return P.ReadOutcome(kind=P.ReadKind.UNKNOWN, observed_at_ms=self._now(), detail='fills_truncated')

        def convert(rows):
            if any(r.order_id != int(exchange_order_id) or r.symbol != symbol for r in rows):
                raise ValueError('fill of another order')
            return tuple(_fill(f) for f in sorted(rows, key=lambda f: (f.time_ms, f.trade_id)))
        return map_read_outcome(t, self._now(), convert)


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

    def funding(self, *, start_ms, end_ms, symbol=None):
        """value: tuple[FundingPayment, ...] for FUNDING_FEE rows in [start_ms, end_ms], oldest first (complete or not
        OK: income_history never returns a partial history)."""
        t = income_history(self._t, start_ms=start_ms, end_ms=end_ms, symbol=symbol, income_type=FUNDING_FEE)
        return map_read_outcome(t, self._clock(), lambda rows: tuple(
            FundingPayment(r.symbol, r.asset, r.income, r.time_ms, r.tran_id) for r in rows))
