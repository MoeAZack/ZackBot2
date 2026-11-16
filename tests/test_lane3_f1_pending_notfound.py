"""LANE3-F1 engine half (found by the adversarial review): a lost close/add (lot['pending']) whose order lookup says
'not found' is decided by the POSITION (filled / unchanged after 20 s / 5-minute resync), never dropped as 'never
reached Binance' on the lookup alone - that would re-send an add or close that did execute, under a new id."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import test_safety as TS
from test_safety import mk_engine, opened, ambiguous_once
E, BC = TS.E, TS.BC


def test_pending_add_with_position_proof_is_not_dropped_on_one_notfound():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q0 = lot['qty']
    ambiguous_once(e, 'open', fill=True)                                 # the add EXECUTED, the answer was lost
    with pytest.raises(BC.AmbiguousOrder): e._add_qty(lot, q0, 100.0, 'pyramid_add')
    assert lot['pending']['kind'] == 'add'
    have = e.trade.pos[('BTCUSDT', 'LONG')]; assert have == pytest.approx(2 * q0)
    e.trade.get_order = lambda s, cid: None                              # the by-id lookup trails / misses (the F1 premise)
    lot['pending']['t'] = time.time() - 21
    res = e._resolve_pending(k, have, q0, 1e-9)
    assert 'pending' in lot or lot['qty'] == pytest.approx(2 * q0), \
        f'res={res}: pending dropped as "never reached Binance" while the position shows the add filled; lot qty {lot["qty"]} vs position {have}'


def test_end_to_end_second_add_is_sent():
    sl = dict(TS.SL, mgmt={'stop_atr': 2.5, 'pyramid': {'n': 1, 'step_r': 0.5, 'frac': 0.5}})
    e, _ = mk_engine(); e.S['SLEEVES'] = [sl]
    assert e.open_lot(sl, 'BTCUSDT', 'LONG', TS.SG, None, e.equity())
    k, lot = next(iter(e.state['lots'].items())); q0 = lot['qty']
    ambiguous_once(e, 'open', fill=True)
    e.trade.mark['BTCUSDT'] = 110.0
    try: e.manage(e.trade.marks())
    except BC.AmbiguousOrder: pass
    assert lot.get('pending', {}).get('kind') == 'add', lot.get('pending')
    after_first = e.trade.pos[('BTCUSDT', 'LONG')]
    e.trade.get_order = lambda s, cid: None
    lot['pending']['t'] = time.time() - 21
    lot.pop('add_retry_at', None)
    e.reconcile(500)
    for _ in range(3):
        lot.pop('add_retry_at', None)
        e.manage(e.trade.marks())
    final = e.trade.pos[('BTCUSDT', 'LONG')]
    assert final == pytest.approx(after_first), f'position grew from {after_first} to {final}: the add was sent twice (lot qty {lot["qty"]})'


def test_not_found_and_position_unchanged_is_released_for_retry_after_20s():
    e, _ = mk_engine(); k = opened(e); lot = e.state['lots'][k]; q0 = lot['qty']
    ambiguous_once(e, 'open', fill=False)                                # it really never executed
    with pytest.raises(BC.AmbiguousOrder): e._add_qty(lot, q0, 100.0, 'pyramid_add')
    have = e.trade.pos[('BTCUSDT', 'LONG')]; assert have == pytest.approx(q0)
    e.trade.get_order = lambda s, cid: None
    assert e._resolve_pending(k, have, q0, 1e-9) is None and 'pending' in lot      # too early: wait
    lot['pending']['t'] = time.time() - 21
    assert e._resolve_pending(k, have, q0, 1e-9) is True and 'pending' not in lot  # position unchanged: free to retry
    assert lot['qty'] == pytest.approx(q0)
