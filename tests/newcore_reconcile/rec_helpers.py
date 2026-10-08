"""Builders for the REC-02 tests.

Two kinds of input:
- pure builders (fact / lot / view / snap / ok / order / pos) for rows FakeVenue has no hook for yet;
- FakeVenue worlds (slice_helpers.World) read through `world_snapshot`, so the journal side is the runner's REAL fold.
  Venue changes FakeVenue has no hook for (a foreign order, a manual close) are modelled on the snapshot or through
  FakeVenue's own fill bookkeeping, in the tests only: no adapter file is changed.
"""
from decimal import Decimal as D

from newcore.domain import EntriesMode, IntentState, Purpose
from newcore.ports.keys import client_id_for
from newcore.ports.venue import (OrderOutcome, OrderRef, OutcomeKind, ReadKind, ReadOutcome, VenueFill, VenueOrder,
                                 VenuePosition)
from newcore.reconcile import (AccountView, IntentFact, LotFact, TradeWindow, VenueSnapshot, plan_reads, reconcile,
                               view_from_fold)
from newcore.runner import InjectedSignals
from slice_helpers import H4, flat_bars

SYM = 'SOLUSDT'
ACCT = 'acct_' + 'a' * 32
T = 1_759_924_800_000
ENTRY_BAR = 5


# ------------------------------------------------------------------------------------------------ pure builders
def iid(n):
    return 'int_' + f'{n:032x}'


def lid(n):
    return 'lot_' + f'{n:032x}'


def cid(n, route='classic'):
    return client_id_for(iid(n), route)


def fact(n, purpose, *, side='LONG', state=IntentState.WORKING, qty='1', stop=None, owner=None, sent=T, eoid=None,
         lookup=None, executed=None, evidence=None, final_at=None, symbol=SYM, route='classic'):
    return IntentFact(intent_id=iid(n), purpose=Purpose(purpose), symbol=symbol, side=side, client_id=cid(n, route),
                      route=route, qty=D(qty), stop_price=None if stop is None else D(stop), state=state,
                      owner_id=owner, sent_at_ms=sent, exchange_order_id=eoid, last_lookup=lookup,
                      final_executed=None if executed is None else D(executed), final_evidence=evidence,
                      final_at_ms=final_at)


def lot(n, *, qty='1', side='LONG', stop_intent=None, stop='90', entry=1, opened=T, symbol=SYM):
    return LotFact(lot_id=lid(n), symbol=symbol, side=side, qty=D(qty), opened_at_ms=opened, entry_intent_id=iid(entry),
                   stop_intent_id=stop_intent, stop_price=None if stop is None else D(stop))


def view(*, lots=(), intents=(), mode=EntriesMode.ACTIVE, hold_kind=None, reasons=(), history=True, newest=None,
         corroboration=(), hints=(), binding=True):
    return AccountView(account_id=ACCT, binding_confirmed=binding, has_history=history, mode=mode, hold_kind=hold_kind,
                       hold_reasons=tuple(reasons), lots=tuple(lots), intents=tuple(intents), newest_result_ms=newest,
                       corroboration=tuple(corroboration), stop_hints=tuple(hints))


def ok(value, at=T):
    return ReadOutcome(kind=ReadKind.OK, observed_at_ms=at, value=tuple(value))


def unknown(at=T):
    return ReadOutcome(kind=ReadKind.UNKNOWN, observed_at_ms=at, detail='timeout')


def pos(qty, *, side='LONG', price='100', symbol=SYM):
    return VenuePosition(symbol=symbol, side=side, qty=D(qty), entry_price=D(price) if D(qty) > 0 else D(0))


def order(client_id, *, side='LONG', qty='1', stop='90', reduce=True, type_='STOP_MARKET', route='classic',
          eoid='5001', symbol=SYM):
    return VenueOrder(ref=OrderRef(symbol=symbol, client_id=client_id, route=route), exchange_order_id=eoid,
                      position_side=side, reduce=reduce, order_type=type_, status='NEW', qty=D(qty),
                      close_position=False, stop_price=None if stop is None else D(stop))


def final(n, executed, *, avg=None, eoid='6001', status=None, at=T, route='classic', symbol=SYM):
    ex = D(executed)
    return OrderOutcome(kind=OutcomeKind.FINAL, ref=OrderRef(symbol=symbol, client_id=cid(n, route), route=route),
                        observed_at_ms=at, status=status or ('FILLED' if ex > 0 else 'CANCELED'), exchange_order_id=eoid,
                        executed_qty=ex, avg_price=None if ex == 0 else D(avg or '100'))


def known(n, *, status='NEW', eoid='6001', at=T, detail=None, route='classic'):
    return OrderOutcome(kind=OutcomeKind.KNOWN, ref=OrderRef(symbol=SYM, client_id=cid(n, route), route=route),
                        observed_at_ms=at, status=status, exchange_order_id=eoid, detail=detail)


def not_found(n, at=T):
    return OrderOutcome(kind=OutcomeKind.NOT_FOUND, ref=OrderRef(symbol=SYM, client_id=cid(n)), observed_at_ms=at,
                        error_code=-2013)


def fill(eoid, qty, price='100', *, trade='1', side='LONG', at=T, symbol=SYM):
    return VenueFill(trade_id=trade, exchange_order_id=eoid, symbol=symbol, position_side=side, qty=D(qty),
                     price=D(price), fee=D('0.01'), fee_asset='USDT', realized_pnl=D(0), maker=False, at_ms=at)


def snap(*, positions=(), orders=(), queries=(), fills=(), trades=(), at=T, pos_read=None, ord_read=None):
    return VenueSnapshot(positions=pos_read or ok(positions, at), orders=ord_read or ok(orders, at),
                         queries=tuple(queries), fills=tuple(fills), trades=tuple(trades))


# ------------------------------------------------------------------------------------------------ FakeVenue worlds
def signals(side='LONG', entry=ENTRY_BAR, exit_=None, more=None):
    sig = {(SYM, flat_bars(1)[0].open_ms + (entry + 1) * H4): (('enter', side),)}
    if exit_ is not None:
        sig[(SYM, flat_bars(1)[0].open_ms + (exit_ + 1) * H4)] = (('close', side),)
    sig.update(more or {})
    return InjectedSignals(sig, stop_atr=D('2'))


def world_snapshot(w, v, *, trades=True, orders_extra=(), transform=None):
    """Fresh reads of the World's FakeVenue at its clock: positions, open orders, a by-id query for every id
    plan_reads asks for, the fills of every order id those answers name, and a userTrades window per side."""
    venue = w.venue
    now = venue.now_ms
    routes = {f.client_id: (f.symbol, f.route) for f in v.intents}
    queries, eoids = [], set()
    for c in plan_reads(v, now_ms=now).queries:
        symbol, route = routes[c]
        q = venue.query(OrderRef(symbol=symbol, client_id=c, route=route))
        queries.append((c, q))
        if q.exchange_order_id is not None:
            eoids.add(q.exchange_order_id)
    fills = [(e, venue.fills(SYM, e)) for e in sorted(eoids)]
    pos_read = venue.positions()
    oo = venue.open_orders()
    oo = ReadOutcome(kind=oo.kind, observed_at_ms=oo.observed_at_ms, value=oo.value + tuple(orders_extra))
    tw = []
    if trades:
        sides = {(p.symbol, p.side) for p in pos_read.value} | {(x.symbol, x.side) for x in v.lots}
        for k in sorted(sides):
            since = min((x.opened_at_ms for x in v.lots if (x.symbol, x.side) == k), default=0)
            rows = tuple(f for f in venue._fills if (f.symbol, f.position_side) == k and f.at_ms >= since)
            tw.append((k, TradeWindow(from_ms=since, read=ok(rows, now))))
    s = VenueSnapshot(positions=pos_read, orders=oo, queries=tuple(queries), fills=tuple(fills), trades=tuple(tw))
    return transform(s) if transform else s


def world_rec(w, *, corroboration=None, stop_hints=None, attempt=0, policy=None, **kw):
    v = view_from_fold(w.runner.fold, corroboration=corroboration, stop_hints=stop_hints)
    s = world_snapshot(w, v, **kw)
    args = dict(now_ms=w.venue.now_ms, attempt=attempt)
    if policy is not None:
        args['policy'] = policy
    return reconcile(v, s, **args), v, s


def kinds(verdict):
    return [(d.kind.value, d.row, d.detail) for d in verdict.decisions]
