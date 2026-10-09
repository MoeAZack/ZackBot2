"""Cowork 6070885320 (S1 at 859af74), ported from v17 a1 / a8 / a3 / a2b + the marker notes:
#1 [HIGH] equal-quantity mask: unproven emergency fills raise HOLD + incident + ownership UNKNOWN whatever the venue holds
   (lot 5, our unjournaled emergency close 2, a foreign add 2 -> venue 5 == lots).
#2 [HIGH] a strategy close while ownership is UNKNOWN is limited to the proven own lower bound (0): nothing closed, loud.
#3 [MED] a stale UNKNOWN recovers: ownership is re-evaluated every cycle while it is UNKNOWN (not only on a position item).
#4 [MED] emergency attribution queries are bounded (a 500-fill order costs a small constant).
marker: only canonical 'complete pages=P dups=N' (ASCII, no leading zeros, 1 <= P <= MAX_PAGES, N <= rows) is evidence."""
import dataclasses
from decimal import Decimal as D

import pytest

from evidence_faults import faulty
from newcore.domain import EntriesMode
from newcore.ports.venue import (MarketOrder, OrderRef, OutcomeKind, ReadKind, ReadOutcome, VenueFill,
                                 VenueOrderRecord)
from newcore.runner import InjectedSignals, ids
from newcore.runner.fill_evidence import rows_of
from slice_helpers import H4, World, flat_bars

pytestmark = pytest.mark.usefixtures('journal_kind')
SYM = 'SOLUSDT'
T0 = flat_bars(1)[0].open_ms
SIDES = ('LONG', 'SHORT')


def at(bar):
    return T0 + (bar + 1) * H4


def position(w, side):
    return sum((p.qty for p in w.venue.positions().value if p.side == side), D(0))


def stops(w, side):
    return [o for o in w.venue.open_orders().value if o.reduce and o.position_side == side
            and o.order_type == 'STOP_MARKET']


def new_orders(w, n):
    return list(w.venue.orders_submitted()[n:])


def emergency_closed(side, foreign, *, close_at=None, cancel=True):
    """Lot 5 of ours; an earlier process's emergency close of 2 (never journaled) left own 3; a foreign add of
    `foreign`. The store is writable throughout."""
    sig = {(SYM, at(5)): (('enter', side),)}
    if close_at is not None:
        sig[(SYM, at(close_at))] = (('close', side),)
    w = World(flat_bars(30), InjectedSignals(sig, stop_atr=D('2')), strict=False)
    w.run(6)
    lot, = w.runner.fold.open_lots()
    assert lot.qty == D('5')
    cid = ids.emergency_stop_client_id(w.runner.acct, SYM, side, D('2'), 'close')
    out = w.venue.submit_market(MarketOrder(ref=OrderRef(symbol=SYM, client_id=cid), position_side=side, qty=D('2'),
                                            reduce=True))
    assert out.kind is OutcomeKind.FINAL
    if foreign:
        w.venue.inject_position(SYM, side, D(foreign), D('100'))
    if cancel:
        w.venue.external_cancel(lot.live_stop.intent.client_order_id)
    return w, lot


# ------------------------------------------------------------------------------------------------------------- #1
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('cancel', (True, False))
def test_1_an_equal_quantity_mask_is_ownership_unknown(side, cancel):
    w, lot = emergency_closed(side, '2', cancel=cancel)
    assert position(w, side) == D('5')                                   # == the lots: no position item
    before = [o.ref.client_id for o in stops(w, side)]
    w.venue.trades = faulty(w.venue.trades, 'unknown')
    w.restart()
    n = len(w.venue.orders_submitted())
    w.run(8)
    r = w.runner
    assert lot.lot_id in r._own_unknown
    assert r.mode is EntriesMode.HOLD
    assert any('ownership UNKNOWN' in t for _, t in r.incidents)
    assert new_orders(w, n) == []                                        # never a stop of 5 over 2 foreign
    assert [o.ref.client_id for o in stops(w, side)] == before           # resting protection kept


# ------------------------------------------------------------------------------------------------------------- #2
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('foreign', ('3', '2'))
def test_2_a_strategy_close_never_closes_foreign_quantity_while_unknown(side, foreign):
    w, lot = emergency_closed(side, foreign, close_at=8, cancel=False)
    w.venue.trades = faulty(w.venue.trades, 'unknown')
    w.restart()
    n = len(w.venue.orders_submitted())
    venue = position(w, side)
    w.run(9)
    r = w.runner
    assert not [o for o in new_orders(w, n) if o.order_type == 'MARKET']
    assert position(w, side) == venue
    assert r.mode is EntriesMode.HOLD
    assert any('NOT sent: ownership UNKNOWN' in t for _, t in r.incidents)


# ------------------------------------------------------------------------------------------------------------- #3
@pytest.mark.parametrize('side', SIDES)
def test_3_a_stale_unknown_recovers_once_the_trades_are_proven(side):
    w, lot = emergency_closed(side, None, cancel=False)                  # venue 3 vs lots 5
    good = w.venue.trades
    w.venue.trades = faulty(good, 'unknown')
    w.run(7)
    assert lot.lot_id in w.runner._own_unknown
    w.venue.inject_position(SYM, side, D('2'), D('100'))                 # foreign 2: venue 5 == lots, no item
    w.run(8)
    w.venue.trades = good
    for b in (9, 10, 11):
        w.run(b)
    r = w.runner
    assert lot.lot_id not in r._own_unknown                              # re-evaluated without a position item
    assert sum((o.qty for o in stops(w, side)), D(0)) == D('3')          # protection = own 3, never the stale 5


# ------------------------------------------------------------------------------------------------------------- #4
def with_rows(read, side, qtys, eoid='77777'):
    """A foreign order of len(qtys) fills on the side, appended to a complete trades page."""
    def call(*a, **k):
        r = read(*a, **k)
        if r.kind is not ReadKind.OK:
            return r
        t = max([f.at_ms for f in r.value] or [r.observed_at_ms - 1])
        extra = tuple(VenueFill(trade_id=f'x{i}', exchange_order_id=eoid, symbol=SYM, position_side=side, qty=q,
                                price=D('100'), fee=D('0'), fee_asset='USDT', realized_pnl=D('0'), maker=False,
                                at_ms=t) for i, q in enumerate(qtys))
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=r.observed_at_ms, value=r.value + extra, detail=r.detail)
    return call


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('how', ('same', 'distinct'))
def test_4_a_500_fill_order_costs_a_bounded_number_of_queries(side, how):
    w, lot = emergency_closed(side, None, cancel=False)
    qtys = [D('0.01')] * 500 if how == 'same' else [D('0.01') * (i + 1) for i in range(500)]
    w.venue.trades = with_rows(w.venue.trades, side, qtys)
    inner, total = w.venue.order_by_id, sum(qtys, D(0))

    def order_by_id(symbol, eoid):                                       # Codex 6071659449: the venue knows the
        if eoid != '77777':                                              # foreign order behind the rows
            return inner(symbol, eoid)
        return ReadOutcome(kind=ReadKind.OK, observed_at_ms=w.venue.now_ms, value=(VenueOrderRecord(
            ref=OrderRef(symbol=SYM, client_id='manual-77777'), exchange_order_id=eoid, position_side=side,
            status='FILLED', orig_qty=total, executed_qty=total),))
    w.port.order_by_id = order_by_id
    r = w.runner
    q0 = w.venue.calls['query']
    ef = r._emergency_filled(SYM, side)
    used = w.venue.calls['query'] - q0
    assert used <= 40, used
    if how == 'same':
        assert ef == D('2')                                              # our emergency close still proven
    else:
        assert ef is None                                                # too many candidates: UNKNOWN, never a fan-out
    q0 = w.venue.calls['query']
    for b in (7, 8):
        w.run(b)
    assert w.venue.calls['query'] - q0 <= 2 * 120                        # whole cycles stay bounded too


# --------------------------------------------------------------------------------------------------------- marker
ROW = VenueFill(trade_id='1', exchange_order_id='7', symbol=SYM, position_side='LONG', qty=D('1'), price=D('100'),
                fee=D('0'), fee_asset='USDT', realized_pnl=D('0'), maker=False, at_ms=T0)


@pytest.mark.parametrize('detail,ok', [
    ('complete pages=1 dups=0', True), ('complete pages=2 dups=1', True),
    ('complete pages=0 dups=0', False), ('complete pages=9999999999 dups=0', False),
    ('complete pages=01 dups=0', False), ('complete pages=1 dups=00', False),
    ('complete pages=١ dups=0', False), ('complete pages=1 dups=٠', False),
    ('complete pages=1 dups=9999999', False), ('complete pages=1 dups=3', False),
    ('complete pages=1 dups=0 ', False), ('Complete pages=1 dups=0', False), ('complete pages=-1 dups=0', False)])
def test_marker_validation(detail, ok):
    rows = (ROW, dataclasses.replace(ROW, trade_id='2'))
    read = ReadOutcome(kind=ReadKind.OK, observed_at_ms=T0, value=rows, detail=detail)
    got, why = rows_of(read, symbol=SYM, side='LONG')
    assert (got is not None) is ok, why
