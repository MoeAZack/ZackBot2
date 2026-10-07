"""AUD-03a (historical audit C03, success-path fill truth): what Binance REPORTS executed is booked, never the request.
Before: an entry answered EXPIRED with executedQty 0 became a full lot with a stop on a position that does not exist; a close
or add booked the requested quantity whatever executed, and close_lot forgot the lot (cancelling its stop) after a partial
close; a pending order was resolved only from the position size, never from the order's own record."""
import pytest
import test_safety as TS
from test_safety import mk_engine, opened, ambiguous_once, SL, SG

E, BC = TS.E, TS.BC


def answer(e, which, status, frac=1.0, cid='zbT'):
    """The next trade.<which> calls answer with `status` and execute frac of the request on the 'exchange'."""
    def f(s, ps, q):
        q = float(q); ex = round(int(q * frac / 0.001 + 1e-9) * 0.001, 3)
        e.trade.pos[(s, ps)] = e.trade.pos.get((s, ps), 0) + (ex if which == 'open' else -ex)
        e.trade.calls.append(which)
        return {'status': status, 'executedQty': str(ex), 'avgPrice': str(e.trade.mark[s]) if ex else '0', 'clientOrderId': cid}
    setattr(e.trade, which, f)


def stop_qty(e, lot):
    return e.trade.stops[lot['stop_id']][2]


# ---------------------------------------------------------------- entries
@pytest.mark.parametrize('status', ['EXPIRED', 'CANCELED'])
def test_entry_with_nothing_executed_opens_no_trade(status):
    e, _ = mk_engine(); answer(e, 'open', status, frac=0.0)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.state['lots'] == {} and e.trade.stops == {} and e.last_skip == 'entry order not filled'
    assert e.trade.pos.get(('BTCUSDT', 'LONG'), 0) == 0


def test_entry_partly_executed_books_the_executed_size_and_protects_exactly_that():
    e, _ = mk_engine(); answer(e, 'open', 'EXPIRED', frac=0.5)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    assert lot['qty'] == pytest.approx(e.trade.pos[('BTCUSDT', 'LONG')]) and stop_qty(e, lot) == pytest.approx(lot['qty'])


def test_entry_answered_not_final_is_unconfirmed_not_a_trade():
    e, _ = mk_engine(); answer(e, 'open', 'NEW', frac=0.0)
    assert not e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert e.state['lots'] == {} and e.last_skip == 'entry order unconfirmed'


# ---------------------------------------------------------------- closes
def test_close_lot_with_nothing_executed_keeps_the_lot_and_its_stop():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q, sid = lot['qty'], lot['stop_id']
    answer(e, 'close', 'EXPIRED', frac=0.0)
    with pytest.raises(E.UnfilledOrder): e.close_lot(k, 'exit_signal', 100.0)
    assert e.state['lots'][k]['qty'] == q and lot['stop_id'] == sid and sid in e.trade.stops and e.history == []


def test_close_lot_partly_executed_keeps_the_rest_as_a_protected_lot():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q = lot['qty']
    answer(e, 'close', 'EXPIRED', frac=0.5)
    with pytest.raises(E.PartialClose): e.close_lot(k, 'exit_signal', 100.0)
    left = e.trade.pos[('BTCUSDT', 'LONG')]
    assert 0 < left < q and e.state['lots'][k]['qty'] == pytest.approx(left)
    assert stop_qty(e, lot) == pytest.approx(left), 'the stop was resized to what is left, not cancelled'
    assert e.history == [], 'nothing is recorded as closed'


def test_tp1_with_nothing_executed_stays_open():
    e, _ = mk_engine(); sl = dict(SL, mgmt={'stop_atr': 2.5, 'tp1_r': 0.5, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values())); q = lot['qty']
    answer(e, 'close', 'EXPIRED', frac=0.0)
    e.trade.mark['BTCUSDT'] = 110.0; e.manage(e.trade.marks())
    assert lot['tp1'] is False and lot['qty'] == q
    assert any('nothing executed' in str(i) for i in e.health['incidents'].values())


def test_tp1_partly_executed_books_only_the_executed_part():
    e, _ = mk_engine(); sl = dict(SL, mgmt={'stop_atr': 2.5, 'tp1_r': 0.5, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values()))
    answer(e, 'close', 'EXPIRED', frac=0.5)                              # half of the half
    e.trade.mark['BTCUSDT'] = 110.0; e.manage(e.trade.marks())
    assert lot['qty'] == pytest.approx(e.trade.pos[('BTCUSDT', 'LONG')]) and stop_qty(e, lot) == pytest.approx(lot['qty'])


# ---------------------------------------------------------------- adds
def _pyramid(e):
    sl = dict(SL, mgmt={'stop_atr': 2.5, 'pyramid': {'n': 1, 'step_r': 0.5, 'frac': 0.5}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    return next(iter(e.state['lots'].values()))


def test_add_with_nothing_executed_changes_nothing_and_cools_down():
    e, _ = mk_engine(); lot = _pyramid(e); q = lot['qty']
    answer(e, 'open', 'EXPIRED', frac=0.0)
    e.trade.mark['BTCUSDT'] = 110.0; e.manage(e.trade.marks())
    assert lot['qty'] == q and lot['adds'] == 0 and lot.get('add_retry_at')


def test_add_partly_executed_books_the_executed_size():
    e, _ = mk_engine(); lot = _pyramid(e)
    answer(e, 'open', 'EXPIRED', frac=0.5)
    e.trade.mark['BTCUSDT'] = 110.0; e.manage(e.trade.marks())
    assert lot['adds'] == 1 and lot['qty'] == pytest.approx(e.trade.pos[('BTCUSDT', 'LONG')])
    assert stop_qty(e, lot) == pytest.approx(lot['qty'])


# ---------------------------------------------------------------- pending orders: the order's own record decides
def _record(e, status, executed):
    e.trade.get_order = lambda s, cid: {'status': status, 'executedQty': str(executed), 'clientOrderId': cid}


def test_close_answered_not_final_is_pending_and_resolved_from_its_order_record():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q = lot['qty']
    answer(e, 'close', 'NEW', frac=0.5)
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 100.0)
    assert lot['pending']['cid'] == 'zbT'
    _record(e, 'EXPIRED', round(q - e.trade.pos[('BTCUSDT', 'LONG')], 3))
    e.reconcile(500)
    assert 'pending' not in lot and lot['qty'] == pytest.approx(e.trade.pos[('BTCUSDT', 'LONG')]) and k in e.state['lots']


def test_pending_close_that_never_executed_books_nothing_even_if_the_position_moved():
    """ENG-B03: a 1-step lot's lost close; the position also changed for another reason. The order record (CANCELED, 0)
    decides: nothing is booked and the pending mark clears - the position size alone would have booked a close."""
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q = lot['qty']
    ambiguous_once(e, 'close', fill=False)
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 100.0)
    assert lot['pending']['cid'] == 'zbtest'
    e.trade.pos[('BTCUSDT', 'LONG')] = 0.0                                  # e.g. its stop filled at the same moment
    _record(e, 'CANCELED', 0)
    e._resolve_pending(k, 0.0, q, 1e-9)
    assert 'pending' not in lot and lot['qty'] == q and e.history == [], 'the stop-out is booked by reconcile, not as this exit'


def test_pending_add_partly_executed_is_booked_from_its_record():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q0 = lot['qty']
    ambiguous_once(e, 'open', fill=False)
    with pytest.raises(BC.AmbiguousOrder): e._add_qty(lot, q0, 100.0, 'pyramid_add')
    e.trade.pos[('BTCUSDT', 'LONG')] = q0 + 0.05
    _record(e, 'EXPIRED', 0.05)
    e.reconcile(500)
    assert 'pending' not in lot and lot['qty'] == pytest.approx(q0 + 0.05)


def test_without_an_order_record_the_position_still_decides():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]
    ambiguous_once(e, 'close')                                             # filled, answer lost; fake has no get_order
    with pytest.raises(BC.AmbiguousOrder): e.close_lot(k, 'exit_signal', 100.0)
    e.reconcile(500)
    assert k not in e.state['lots'] and e.history[-1]['exit_reason'] == 'exit_signal'


# ---------------------------------------------------------------- the client returns a final record
def test_client_returns_a_final_non_filled_record_instead_of_ambiguous(monkeypatch):
    f = BC.Futures.__new__(BC.Futures)
    def req(m, path, params=None, signed=False, retry=None, critical=None):
        if m == 'POST': raise BC.AmbiguousOrder('timeout')
        return {'status': 'EXPIRED', 'executedQty': '0.004', 'clientOrderId': params['origClientOrderId']}
    f._req = req
    monkeypatch.setattr(BC.time, 'sleep', lambda s: None)
    o = f.close('BTCUSDT', 'LONG', '0.010')
    assert o['status'] == 'EXPIRED' and o['executedQty'] == '0.004'


def test_exec_of_rules():
    x = E.Engine._exec_of
    assert x({'status': 'FILLED', 'executedQty': '0.010'}, 0.010, 0.001) == pytest.approx(0.010)
    assert x({'status': 'FILLED'}, 0.010, 0.001) == pytest.approx(0.010)
    assert x({'status': 'EXPIRED', 'executedQty': '0'}, 0.010, 0.001) == 0
    assert x({'status': 'EXPIRED'}, 0.010, 0.001) is None
    assert x({'status': 'NEW', 'executedQty': '0'}, 0.010, 0.001) is None
    assert x({'status': 'PARTIALLY_FILLED', 'executedQty': '0.004'}, 0.010, 0.001) is None
    assert x({'status': 'FILLED', 'executedQty': '0.020'}, 0.010, 0.001) == pytest.approx(0.010), 'capped at the request'
    assert x({'avgPrice': '100'}, 0.010, 0.001) == pytest.approx(0.010), 'simulated answer without status: unchanged'
