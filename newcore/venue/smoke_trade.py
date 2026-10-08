"""S5 owner smoke: the printed PLAN and the optional TESTNET trade phase (tools/newcore_smoke.py --trade).

Read-only first (newcore.venue.smoke.run_smoke). Only with --trade, and only if every read was OK, the account is in
hedge mode, FLAT, and the symbol's rules are known, ONE minimum-size round trip runs through the TestnetVenue port:
  price (last CLOSED 1m candle) -> MARKET BUY LONG (min feasible qty) -> reduce-only STOP_MARKET (algo route by default)
  -> query the stop -> cancel it and confirm -> MARKET SELL LONG (reduce) -> flat check -> fills (fees).
Client ids are derived like the Runner's (newcore.ports.keys): one id per intent, unique per run.
Safety: the transport is hard-pinned to the Binance Futures TESTNET host. Nothing is retried in a loop: an entry or a
close whose state is unknown is queried ONCE by client id; any path that may leave exposure without a resting stop
triggers ONE immediate reduce-only close and a loud manual-check message naming the client ids.
"""
import hashlib
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from newcore.ports import keys as K
from newcore.ports import venue as P

from .outcomes import ReadKind as TR

PRICE_INTERVAL = '1m'
NOTIONAL_HEADROOM = Decimal('1.1')         # 10 % above min notional so a small move cannot trip -4164
STOP_DISTANCE = Decimal('0.95')            # LONG stop 5 % below the entry fill
ROUTES = ('algo', 'classic')

READ_PLAN = (
    ('calibrate clock', 'GET /fapi/v1/time x3 (unsigned)', 'server offset + RTT'),
    ('exchange rules', 'GET /fapi/v1/exchangeInfo (unsigned)', 'core-8 tick / step / min qty / min notional'),
    ('balances', 'GET /fapi/v2/account', 'wallet / available'),
    ('positions', 'GET /fapi/v2/positionRisk', 'expected flat'),
    ('position mode', 'GET /fapi/v1/positionSide/dual', 'must be hedge'),
    ('open orders', 'GET /fapi/v1/openOrders', 'expected none'),
    ('open algo orders', 'GET /fapi/v1/openAlgoOrders', 'expected none'),
    ('income 7d', 'GET /fapi/v1/income (time-window pages)', 'funding / commission / realized pnl'),
)
TRADE_PLAN = (
    ('preflight', '(no request)', 'refuse unless all reads OK, hedge mode, flat, symbol rules known'),
    ('price', 'GET /fapi/v1/klines 1m limit 3 (unsigned)', 'last CLOSED candle close'),
    ('entry', 'POST /fapi/v1/order MARKET BUY LONG', 'min feasible qty (>= min qty, >= 1.1 x min notional)'),
    ('protect', 'POST /fapi/v1/algoOrder (or /order) STOP_MARKET SELL LONG',
     'reduce-only, mark price, trigger 5 % below the fill'),
    ('verify stop', 'GET algo order / order by client id', 'must be resting'),
    ('cancel stop', 'DELETE algo order / order by client id, then GET to confirm', 'CANCELED, nothing executed'),
    ('close', 'POST /fapi/v1/order MARKET SELL LONG (reduce)', 'the entry quantity'),
    ('flat check', 'GET /fapi/v2/positionRisk + openOrders + openAlgoOrders', 'flat, no order of ours left'),
    ('fees', 'GET /fapi/v1/userTrades for the entry and the close', 'fee + realized pnl truth'),
)


def format_plan(*, trade, symbol, stop_route, cassette_path, account_id, credentials_stored):
    lines = ['NEWCORE S5 testnet smoke - PLAN (dry run: no network call is made, no key is decrypted)',
             f'  account id      : {account_id}   stored testnet key: {"yes" if credentials_stored else "NO"}',
             '  venue           : https://testnet.binancefuture.com (hard-pinned; mainnet impossible)',
             f'  cassette        : {cassette_path} (sanitized, leak-checked; never in the repo)', '',
             'READ-ONLY phase:']
    lines += [f'  {i}. {n:<16} {r:<48} {w}' for i, (n, r, w) in enumerate(READ_PLAN, 1)]
    if trade:
        lines += ['', f'TRADE phase (--trade): ONE minimum-size round trip on {symbol}, stop route {stop_route}:']
        lines += [f'  {i}. {n:<16} {r:<48} {w}' for i, (n, r, w) in enumerate(TRADE_PLAN, 1)]
        lines += ['  Any unknown state is queried ONCE by client id; possible exposure without a resting stop is closed',
                  '  at once (reduce-only) and reported with the client ids for a manual check. Nothing is retried.']
    else:
        lines += ['', 'TRADE phase: not requested (add --trade). No order will be placed.']
    return '\n'.join(lines)


# ---------------------------------------------------------------------------------------------- ids + sizing
def account_ref(account_uuid):
    return 'acct_' + account_uuid.replace('-', '').lower()


@dataclass(frozen=True)
class SmokeIds:
    entry: P.OrderRef
    stop: P.OrderRef
    close: P.OrderRef
    entry_intent: str
    stop_intent: str
    close_intent: str


def smoke_ids(account_uuid, run_ms, symbol, stop_route):
    """One intent per order, unique per run; client ids exactly as the Runner derives them."""
    acct = account_ref(account_uuid)
    entry_intent = 'int_' + hashlib.sha256(b'zackbot.newcore.smoke.entry.v1\x00' + acct.encode('ascii') + b'\x00'
                                           + str(run_ms).encode('ascii')).hexdigest()[:32]
    stop_intent = K.derive_child_intent_id(acct, entry_intent, 'protect', 0)
    close_intent = K.derive_child_intent_id(acct, K.derive_lot_id(acct, entry_intent), 'close', 0)
    return SmokeIds(entry=P.OrderRef(symbol=symbol, client_id=K.client_id_for(entry_intent)),
                    stop=P.OrderRef(symbol=symbol, client_id=K.client_id_for(stop_intent, stop_route), route=stop_route),
                    close=P.OrderRef(symbol=symbol, client_id=K.client_id_for(close_intent)),
                    entry_intent=entry_intent, stop_intent=stop_intent, close_intent=close_intent)


def _ceil_to(x, step):
    return (x / step).to_integral_value(rounding=ROUND_CEILING) * step


def _floor_to(x, step):
    return (x / step).to_integral_value(rounding=ROUND_FLOOR) * step


def min_feasible_qty(rules, price):
    """The smallest MARKET quantity that satisfies min qty and 1.1 x min notional (rounded UP to the market step:
    this is a mechanics smoke, not risk sizing). None if it would exceed the market max qty."""
    step = rules.market_step_size
    qty = max(rules.market_min_qty, _ceil_to(rules.min_notional * NOTIONAL_HEADROOM / price, step))
    qty = _ceil_to(qty, step).quantize(step)
    return None if qty > rules.market_max_qty else qty


def stop_price_for(avg, rules):
    return _floor_to(avg * STOP_DISTANCE, rules.tick_size)


# ---------------------------------------------------------------------------------------------- the trade phase
class TradeAborted(Exception):
    """The trade phase stopped. exposure_possible says whether a position may be left; message names client ids."""

    def __init__(self, message, exposure_possible=False):
        super().__init__(message)
        self.exposure_possible = exposure_possible


@dataclass
class TradeReport:
    symbol: str
    qty: Decimal = None
    entry_avg: Decimal = None
    stop_price: Decimal = None
    close_avg: Decimal = None
    fees: Decimal = None
    realized_pnl: Decimal = None
    steps: list = field(default_factory=list)


def _final(venue, ref, outcome, what, rep):
    """FINAL as is; KNOWN / UNKNOWN / ACKNOWLEDGED are queried ONCE by client id. Returns the FINAL outcome or None."""
    rep.steps.append(f'{what}: {outcome.kind.value}' + (f' ({outcome.detail})' if outcome.detail else ''))
    if outcome.kind is P.OutcomeKind.FINAL:
        return outcome
    if outcome.kind in (P.OutcomeKind.REJECTED,):
        return None
    q = venue.query(ref)
    rep.steps.append(f'{what} query by client id: {q.kind.value}' + (f' ({q.detail})' if q.detail else ''))
    return q if q.kind is P.OutcomeKind.FINAL else None


def run_trade_smoke(venue, transport, read_report, *, symbol, ids, clock):
    rep = TradeReport(symbol=symbol)
    # 1. preflight
    if read_report.hedge is not True:
        raise TradeAborted('refused: the account is not in hedge mode')
    if not read_report.flat:
        raise TradeAborted('refused: the account is not flat (positions or open orders)')
    rules = read_report.rules.get(symbol)
    if rules is None:
        raise TradeAborted(f'refused: no exchange rules for {symbol}')
    # 2. price: last CLOSED candle
    k = transport.klines(symbol, PRICE_INTERVAL, limit=3)
    if k.kind is not TR.OK:
        raise TradeAborted(f'refused: price read {k.kind.value}')
    now = clock()
    closed = [c for c in k.value if c.is_closed_at(now)]
    if not closed:
        raise TradeAborted('refused: no closed candle to price from')
    qty = min_feasible_qty(rules, closed[-1].close)
    if qty is None:
        raise TradeAborted('refused: the minimum feasible quantity exceeds the market max qty')
    rep.qty = qty
    # 3. entry
    sent = venue.submit_market(P.MarketOrder(ref=ids.entry, position_side='LONG', qty=qty, reduce=False))
    if sent.kind is P.OutcomeKind.REJECTED:
        raise TradeAborted(f'entry refused by the venue (code {sent.error_code}, {sent.detail}); nothing executed')
    entry = _final(venue, ids.entry, sent, 'entry', rep)
    if entry is None:
        raise TradeAborted(f'ENTRY STATE NOT FINAL: check testnet for client id {ids.entry.client_id}; nothing else '
                           f'was sent', exposure_possible=True)
    if entry.executed_qty == 0:
        raise TradeAborted('entry ended without a fill (nothing executed); stopping cleanly')
    filled, rep.entry_avg = entry.executed_qty, entry.avg_price

    def emergency_close(reason):
        out = _final(venue, ids.close, venue.submit_market(P.MarketOrder(ref=ids.close, position_side='LONG',
                                                                          qty=filled, reduce=True)), 'emergency close',
                     rep)
        ok = out is not None and out.executed_qty == filled
        raise TradeAborted(f'{reason}; emergency reduce-only close {"DONE" if ok else "NOT CONFIRMED"} '
                           f'(entry {ids.entry.client_id}, close {ids.close.client_id})', exposure_possible=not ok)

    # 4. protect
    rep.stop_price = stop_price_for(rep.entry_avg, rules)
    stop = venue.submit_stop(P.StopOrder(ref=ids.stop, position_side='LONG', qty=filled, stop_price=rep.stop_price))
    rep.steps.append(f'protect: {stop.kind.value}' + (f' ({stop.detail})' if stop.detail else ''))
    if stop.kind is not P.OutcomeKind.KNOWN:
        emergency_close(f'the protective stop did not rest ({stop.kind.value})')
    # 5. verify
    v = venue.query(ids.stop)
    rep.steps.append(f'verify stop: {v.kind.value} {v.status or ""}'.rstrip())
    if v.kind is not P.OutcomeKind.KNOWN:
        emergency_close(f'the protective stop could not be verified ({v.kind.value})')
    # 6. cancel + confirm
    c = venue.cancel(ids.stop)
    rep.steps.append(f'cancel stop: {c.kind.value}')
    confirm = c if c.kind is P.OutcomeKind.FINAL else venue.query(ids.stop)
    if confirm is not c:
        rep.steps.append(f'confirm cancel: {confirm.kind.value} {confirm.status or ""}'.rstrip())
    stop_gone = confirm.kind is P.OutcomeKind.FINAL and confirm.executed_qty == 0
    # 7. close (always: the position must not outlive the smoke)
    close = _final(venue, ids.close, venue.submit_market(P.MarketOrder(ref=ids.close, position_side='LONG', qty=filled,
                                                                        reduce=True)), 'close', rep)
    if close is None or close.executed_qty != filled:
        raise TradeAborted(f'CLOSE NOT CONFIRMED: check testnet for client id {ids.close.client_id} '
                           f'(entry {ids.entry.client_id})', exposure_possible=True)
    rep.close_avg = close.avg_price
    if not stop_gone:
        raise TradeAborted(f'the stop cancel was not confirmed: check testnet for client id {ids.stop.client_id} '
                           f'(position closed)')
    # 8. flat check
    pos, oo = venue.positions(symbol), venue.open_orders(symbol)
    if pos.kind is not P.ReadKind.OK or oo.kind is not P.ReadKind.OK:
        raise TradeAborted('flat check unreadable after the close: check testnet manually', exposure_possible=True)
    if any(p.qty != 0 for p in pos.value):
        raise TradeAborted('NOT FLAT after the close: check testnet manually', exposure_possible=True)
    ours = {ids.entry.client_id, ids.stop.client_id, ids.close.client_id}
    if any(o.ref.client_id in ours for o in oo.value):
        raise TradeAborted('an order of this smoke is still open: check testnet manually')
    rep.steps.append('flat check: flat, no order of ours open')
    # 9. fees
    fees, pnl = Decimal(0), Decimal(0)
    for o in (entry, close):
        f = venue.fills(symbol, o.exchange_order_id)
        if f.kind is not P.ReadKind.OK:
            raise TradeAborted(f'fills of order {o.exchange_order_id} not readable ({f.kind.value})')
        fees += sum((x.fee for x in f.value), Decimal(0))
        pnl += sum((x.realized_pnl for x in f.value), Decimal(0))
    rep.fees, rep.realized_pnl = fees, pnl
    rep.steps.append('fees: read from userTrades')
    return rep


def format_trade_report(rep):
    return '\n'.join([f'TRADE {rep.symbol}: qty {rep.qty}  entry avg {rep.entry_avg}  stop {rep.stop_price}  '
                      f'close avg {rep.close_avg}  fees {rep.fees}  realized pnl {rep.realized_pnl}']
                     + [f'  - {s}' for s in rep.steps])
