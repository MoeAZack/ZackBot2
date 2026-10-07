"""AUD-02 (historical audit C02 + C09): management-stage isolation and pending/resting-aware closes.
C02 / ENG-B02: one try-block per lot let a repeatedly rejected add (e.g. -2019 margin insufficient) skip every later stage,
so the trailing stop never moved (repro: stop stayed 95 while price ran 100 -> 130).
C09 / ENG-B05/B06: close_lot ignored `pending` and closed the whole exchange position when it was the last lot on a side,
which could take a sibling's size after a filled pending close, or another slot's resting-maker fill."""
import time
import pytest
import test_safety as TS
from test_safety import mk_engine, SL, SG

E, BC = TS.E, TS.BC


def _trail_pyramid(e):
    sl = dict(SL, mgmt={'stop_atr': 2.5, 'trail_atr': 1.0, 'pyramid': {'n': 1, 'step_r': 0.5, 'frac': 0.5}}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    return next(iter(e.state['lots'].items()))


def _reject_open(e, code=-2019):
    def open_(s, ps, q):
        e.trade.calls.append('open'); raise BC.BinanceError(code, 'Margin is insufficient.')
    e.trade.open = open_


def test_a_rejected_add_no_longer_starves_the_trailing_stop():
    e, _ = mk_engine(); k, lot = _trail_pyramid(e)
    stop0 = lot['stop']; _reject_open(e)
    for px in (104.0, 110.0, 120.0, 130.0):
        e.trade.mark['BTCUSDT'] = px; e.manage(e.trade.marks())
    assert lot['best'] == 130.0 and lot['stop'] > stop0 + 20, (stop0, lot['stop'])     # the trail followed price
    assert lot['adds'] == 0 and any('pyramid_add failed' in str(i) for i in e.health['incidents'].values())


def test_a_rejected_add_is_cooled_down_not_retried_every_pass(monkeypatch):
    e, _ = mk_engine(); k, lot = _trail_pyramid(e); _reject_open(e)
    e.trade.mark['BTCUSDT'] = 110.0
    for _ in range(5): e.manage(e.trade.marks())
    assert e.trade.calls.count('open') == 1 + 1, 'entry + ONE rejected add (then cooled down)'
    assert lot['add_retry_at'] - time.time() > E.ADD_RETRY_S - 5                       # business refusal: long cooldown
    real = time.time
    monkeypatch.setattr(E.time, 'time', lambda: real() + E.ADD_RETRY_S + 1)           # cooldown over -> tried again
    e.manage(e.trade.marks())
    assert e.trade.calls.count('open') == 3


def test_a_transient_add_failure_uses_the_short_cooldown():
    e, _ = mk_engine(); k, lot = _trail_pyramid(e); _reject_open(e, code=-1001)        # disconnected: transient
    e.trade.mark['BTCUSDT'] = 110.0; e.manage(e.trade.marks())
    assert 0 < lot['add_retry_at'] - time.time() <= E.ADD_RETRY_TRANSIENT_S + 1


def test_a_failing_exit_does_not_stop_the_trailing_stop():
    e, _ = mk_engine()
    sl = dict(SL, mgmt={'stop_atr': 2.5, 'trail_atr': 1.0, 'tp1_r': 0.5, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    lot = next(iter(e.state['lots'].values())); stop0 = lot['stop']
    def close_(s, ps, q): e.trade.calls.append('close'); raise BC.BinanceError(-2022, 'ReduceOnly Order is rejected.')
    e.trade.close = close_
    for px in (110.0, 120.0, 130.0):
        e.trade.mark['BTCUSDT'] = px; e.manage(e.trade.marks())
    assert lot['tp1'] is False and lot['stop'] > stop0 + 20                               # tp1 failed, the trail still ran


def test_an_unanswered_add_still_aborts_the_lot_for_this_pass():
    """AmbiguousOrder keeps its old meaning: the lot becomes pending and nothing else is done to it this pass."""
    e, _ = mk_engine(); k, lot = _trail_pyramid(e); stop0 = lot['stop']
    TS.ambiguous_once(e, 'open')
    e.trade.mark['BTCUSDT'] = 130.0; e.manage(e.trade.marks())
    assert lot.get('pending') and lot['stop'] == stop0


def test_close_is_refused_while_an_order_of_the_lot_is_unconfirmed():
    e, _ = mk_engine()
    sl = dict(SL, mgmt={'tp1_r': 1.0, 'tp1_frac': 0.5}); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots'])); lot = e.state['lots'][k]
    lot['pending'] = dict(kind='close', qty=lot['qty'] / 2, px=120.0, why='take_profit_1', post={'tp1': True}, t=time.time())
    n_close = e.trade.calls.count('close')
    with pytest.raises(RuntimeError, match='close deferred'):
        e.close_lot(k, 'closed_from_panel')
    res = e.flatten() if hasattr(e, 'flatten') else None
    assert e.trade.calls.count('close') == n_close and k in e.state['lots']
    if res is not None: assert [x[0] for x in res['failed']] == [k] and 'close deferred' in res['failed'][0][1]


def test_closing_the_last_lot_never_takes_another_slots_resting_fill():
    """Slot A's lot + 0.1 BTC from slot B's partially filled maker entry ($10 at $100, above the $5 minimum) on the same
    side: closing A sends A's own quantity; B's fill stays on the exchange for its own lot."""
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots'])); q = e.state['lots'][k]['qty']
    e.trade.pos[('BTCUSDT', 'LONG')] = q + 0.1
    e.close_lot(k, 'exit_signal')
    assert k not in e.state['lots'] and e.trade.pos[('BTCUSDT', 'LONG')] == pytest.approx(0.1)


def test_closing_the_last_lot_still_sweeps_dust():
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots'])); q = e.state['lots'][k]['qty']
    e.trade.pos[('BTCUSDT', 'LONG')] = q + 0.004                                          # $0.40 at $100: dust
    e.close_lot(k, 'exit_signal')
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')]) < 1e-9


def test_closing_when_the_exchange_holds_less_closes_what_is_held():
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    k = next(iter(e.state['lots'])); q = e.state['lots'][k]['qty']
    e.trade.pos[('BTCUSDT', 'LONG')] = q - 0.002
    e.close_lot(k, 'exit_signal')
    assert abs(e.trade.pos[('BTCUSDT', 'LONG')]) < 1e-9
