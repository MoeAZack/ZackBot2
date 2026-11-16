"""CAPS-01 r2 (Codex #365 review, #219 c6925536536): the caps check also covers the paths that opened risk around it -
an active grid opening a new side or adding, a resting maker entry re-pricing or falling back to market - and the
open-risk cap counts entry intents that are not lots yet (resting maker entries, unconfirmed entries, pending adds)
exactly once. Stops, exits and management keep running while the caps are non-compliant. Offline fakes only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_grid as TG                                                     # noqa: E402
import test_v31_engine as TV                                               # noqa: E402
from test_safety import mk_engine, SL, SG                                 # noqa: E402

LOOSE = [dict(PEAK_DD_FLATTEN=0.0), dict(DAILY_LOSS_HALT=0.0), dict(RISK_RULES={'open_risk_cap': {'mode': 'warn', 'pct': 2.5}})]


# ---------------------------------------------------------------- grid
@pytest.mark.parametrize('loose', LOOSE)
def test_r2_an_active_grid_opens_no_new_side_after_caps_turn_loose(loose):
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    e.S.update(loose)
    assert e.risk_caps_problems()
    n_open = e.trade.calls.count('open')
    TG.tick(e, gm, max(c['a'] for c in g['cells'] if c['k'] == 'L') - 0.05)     # first buy line crossed
    assert TG.lot_of(e, 'LONG') is None and e.trade.calls.count('open') == n_open and not g.get('op')
    noise = [k for k in e.health.get('incidents', {}) if 'grid' in k and 'not sent' in k]
    assert not noise, f'refused at the send instead of the grid gate (an order op was written first): {noise}'


@pytest.mark.parametrize('loose', LOOSE)
def test_r2_an_existing_grid_lot_gets_no_add_after_caps_turn_loose_but_keeps_its_stop(loose):
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    lines = sorted(c['a'] for c in g['cells'] if c['k'] == 'L')
    TG.tick(e, gm, lines[-1] - 0.05)
    L = TG.lot_of(e, 'LONG'); q = L['qty']; assert L and L['stop_id']
    e.S.update(loose)
    TG.tick(e, gm, lines[-2] - 0.05)                                            # next line down: would be an add
    assert L['qty'] == q and sum(c['f'] for c in g['cells']) == 1
    assert L['stop_id'] in e.trade.stops and abs(e.trade.stops[L['stop_id']][2] - q) < 1e-9
    TG.consistent(e)


def test_r2_the_grid_send_gate_refuses_too():
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e); e.S.update(PEAK_DD_FLATTEN=0.0)
    with pytest.raises(ValueError, match='risk caps looser than policy'): gm._order_ok(g, 0.001, 100.0)


def test_r2_a_compliant_grid_still_opens_and_adds():
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e); lines = sorted(c['a'] for c in g['cells'] if c['k'] == 'L')
    TG.tick(e, gm, lines[-1] - 0.05); TG.tick(e, gm, lines[-2] - 0.05)
    assert sum(c['f'] for c in g['cells']) == 2; TG.consistent(e)


# ---------------------------------------------------------------- maker
@pytest.mark.parametrize('loose', LOOSE)
def test_r2_a_resting_maker_entry_is_not_repriced_and_gets_no_fallback(loose):
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    assert len(bk.orders) == 1
    e.S.update(loose)
    e.manage(e.trade.marks())                                                   # no waiting: the working order is cancelled now
    assert next(iter(bk.orders.values()))['status'] == 'CANCELED'
    for _ in range(6): TV.age(e, total=True); e.manage(e.trade.marks())
    assert len(bk.orders) == 1, 'a fresh LIMIT entry was placed while the caps were loose'
    assert all(o['status'] == 'CANCELED' for o in bk.orders.values())
    assert 'open' not in e.trade.calls and not e.state['lots'] and not e.state['resting_entries']


def test_r2_a_partly_filled_maker_entry_keeps_its_fill_with_a_stop_but_no_market_remainder():
    e, bk = TV.maker_engine()
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    target = next(iter(e.state['resting_entries'].values()))['qty']
    bk.fill(0.4)
    e.S.update(PEAK_DD_FLATTEN=0.0)
    for _ in range(6): TV.age(e, total=True); e.manage(e.trade.marks())
    lot = TV.lot_of(e)
    assert lot and abs(lot['qty'] - round(target * .4, 3)) < 1e-9 and 'open' not in e.trade.calls
    assert lot['stop_id'] and abs(TV.exch_stop(e, lot)[2] - lot['qty']) < 1e-9


def test_r2_the_maker_send_gate_refuses_a_new_resting_order():
    e, bk = TV.maker_engine()
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    e.S.update(DAILY_LOSS_HALT=0.0)
    with pytest.raises(RuntimeError, match='risk caps looser than policy'): e._maker_place(rec)
    assert len(bk.orders) == 1


# ---------------------------------------------------------------- reserved risk
def _cap(e): return e.guard_eq or e.last_eq


def _resting(e, pct, sym='ETHUSDT', n=0):
    d = 2.0; q = pct / 100 * _cap(e) / d
    k = f'ME|X{n}|{sym}|LONG'
    e.state['resting_entries'][k] = dict(key=k, sleeve='X', symbol=sym, side='LONG', qty=q, filled=0.0, cost=0.0, n=1,
                                         t0=time.time(), status='open', cid=f'zm{n}', plan=dict(stop_dist=d, qty=q))
    return k


def test_r2_three_one_percent_maker_intents_do_not_pass_a_2_5_percent_cap():
    e, _ = mk_engine()
    one = 0.01 * _cap(e)
    assert e.entry_block(SL, 'BTCUSDT', 'LONG', size=dict(risk=one)) is None
    _resting(e, 1.0, n=1); _resting(e, 1.0, n=2)                          # two 1% intents still resting (other sleeves)
    blk = e.entry_block(SL, 'BTCUSDT', 'LONG', size=dict(risk=one))
    assert blk and 'open_risk_cap' in blk, blk


def test_r2_unconfirmed_entries_and_pending_adds_are_reserved():
    e, _ = mk_engine()
    cap = _cap(e)
    e.state['unconfirmed_entries']['UE|X|ETHUSDT|LONG|1'] = dict(symbol='ETHUSDT', side='LONG', cid='zx', qty=0.01 * cap / 2.0,
                                                                  t=time.time(), prov=None, prov_qty=0.0, plan=dict(stop_dist=2.0))
    assert e._reserved_risk() == pytest.approx(0.01 * cap)
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = TV.lot_of(e)
    lot['pending'] = dict(kind='add', qty=0.5, px=lot['avg'], why='dca', t=time.time(), cid='zp')
    assert e._reserved_risk() == pytest.approx(0.01 * cap + 0.5 * (lot['avg'] - lot['stop']))
    lot['pending'] = dict(kind='close', qty=0.5, px=lot['avg'], why='x', t=time.time(), cid='zc')     # a close reserves nothing
    assert e._reserved_risk() == pytest.approx(0.01 * cap)


def test_r2_a_maker_intent_is_counted_once_before_and_after_it_becomes_a_lot():
    e, bk = TV.maker_engine()
    e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values()))
    before = e._reserved_risk()
    assert before == pytest.approx(rec['plan']['stop_dist'] * rec['qty']) and before > 0
    bk.fill(); e.manage(e.trade.marks())
    lot = TV.lot_of(e)
    assert lot and not e.state['resting_entries'] and e._reserved_risk() == 0.0
    assert e._lot_risk(lot) > 0


def test_r2_an_older_plan_without_stop_distance_reserves_its_planned_risk():
    assert E.Engine._plan_risk(dict(risk_usd=7.5), 3.0) == 7.5
    assert E.Engine._plan_risk(dict(stop_dist=2.0, risk_usd=99), 3.0) == 6.0
    assert E.Engine._plan_risk(None, 3.0) == 0.0 and E.Engine._plan_risk(dict(stop_dist=float('nan'), risk_usd=4), 1) == 4.0
