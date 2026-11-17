"""CAPS-01 r4 (Codex #365 round 4, c6927871631): (1)(2) an entry intent whose risk cannot be computed refuses new risk
(never counted as zero); (3) a sent maker order whose lookup says 'not found' keeps its ownership and reservation (handed
to the unconfirmed-entry path, partial position protected) - never dropped; (4) a grid order must fit the portfolio
open-risk budget (lots + reserved intents), not only a compliant configuration. Offline fakes only."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('LOCALAPPDATA', tempfile.mkdtemp())
import pytest
import engine as E
import test_grid as TG                                                     # noqa: E402
import test_v31_engine as TV                                               # noqa: E402
from test_safety import mk_engine, SL, SG                                 # noqa: E402


def _cap(e): return e.guard_eq or e.last_eq


# ---------------------------------------------------------------- 1 / 2: unknown risk fails closed
@pytest.mark.parametrize('plan', [dict(qty=1.0), dict(qty=1.0, stop_dist=None, risk_usd=None), dict(qty=1.0, stop_dist='x'), None])
def test_r4_a_resting_entry_with_unknown_risk_refuses_new_risk(plan):
    e, _ = mk_engine()
    assert e.entry_block(SL, 'BTCUSDT', 'LONG', size=dict(risk=0.001 * _cap(e))) is None
    e.state['resting_entries']['ME|X|ETHUSDT|LONG'] = dict(key='ME|X|ETHUSDT|LONG', sleeve='X', symbol='ETHUSDT', side='LONG',
                                                          qty=1.0, filled=0.0, cost=0.0, n=1, t0=time.time(), status='open',
                                                          cid='zm1', plan=plan)
    assert e._reserved_risk() is None
    blk = e.entry_block(SL, 'BTCUSDT', 'LONG', size=dict(risk=0.001 * _cap(e)))
    assert blk and E.OPEN_RISK_UNKNOWN in blk, blk
    assert e.open_risk_room(0.0)


@pytest.mark.parametrize('drop', ['px', 'qty'])
def test_r4_a_pending_add_with_unknown_price_or_size_refuses_new_risk(drop):
    e, _ = mk_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity()), e.last_skip
    lot = TV.lot_of(e)
    p = dict(kind='add', qty=0.5, px=lot['avg'], why='dca', t=time.time(), cid='zp'); p.pop(drop)
    lot['pending'] = p
    assert e._reserved_risk() is None
    blk = e.entry_block(SL, 'ETHUSDT', 'LONG', manual=True, size=dict(risk=0.001 * _cap(e)))
    assert blk and E.OPEN_RISK_UNKNOWN in blk, blk


def test_r4_unconfirmed_entry_with_unknown_risk_refuses_too():
    e, _ = mk_engine()
    e.state['unconfirmed_entries']['UE|X|ETHUSDT|LONG|1'] = dict(symbol='ETHUSDT', side='LONG', cid='zx', qty=1.0, t=time.time(),
                                                                  prov=None, prov_qty=0.0, plan=dict(px=100.0))
    assert e._reserved_risk() is None and E.OPEN_RISK_UNKNOWN in (e.entry_block(SL, 'BTCUSDT', 'LONG') or '')


# ---------------------------------------------------------------- 3: maker lookup 'not found' keeps ownership
def _partial_then_lost(e, bk):
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values())); cid, q = rec['cid'], rec['qty']
    bk.fill(0.4)                                                              # partial fill on Binance (record not final)
    TV.age(e, 30); e.manage(e.trade.marks())                                  # slice over -> cancel requested
    assert rec['status'] == 'cancelling'
    e.trade.get_order = lambda s, c: None                                     # the lookup now says 'not found'
    TV.age(e, 30); e.manage(e.trade.marks())
    return cid, q


def test_r4_a_maker_order_not_found_after_cancel_is_never_dropped():
    e, bk = TV.maker_engine()
    cid, q = _partial_then_lost(e, bk)
    assert not e.state['resting_entries']
    (uk, u), = e.state['unconfirmed_entries'].items()                         # ownership + reservation handed over
    assert u['cid'] == cid and u['qty'] == pytest.approx(q)
    assert e._reserved_risk() > 0
    assert 'not confirmed' in (e.entry_block(SL, 'BTCUSDT', 'LONG', manual=True) or '')   # no second entry on the coin/side
    e.reconcile(500); e.reconcile(500)
    assert u.get('prov') in e.trade.stops                                     # the partial position is protected
    assert abs(e.trade.stops[u['prov']][2] - round(q * .4, 3)) < 1e-9
    assert not e.untracked and 'open' not in e.trade.calls                    # no market fallback


def test_r4_its_final_record_then_settles_the_partial_fill_into_a_lot_with_one_stop():
    e, bk = TV.maker_engine()
    cid, q = _partial_then_lost(e, bk)
    e.reconcile(500)
    e.trade.get_order = bk.get                                                # the record is readable again: CANCELED, 40 %
    e.reconcile(500)
    lot = TV.lot_of(e)
    assert lot and lot['qty'] == pytest.approx(round(q * .4, 3)) and e.state['unconfirmed_entries'] == {}
    e.manage(e.trade.marks())
    st = [v for v in e.trade.stops.values() if v[0] == 'BTCUSDT' and v[1] == 'LONG']
    assert len(st) == 1 and abs(st[0][2] - lot['qty']) < 1e-9


def test_r4_a_never_visible_maker_order_is_not_forgotten_either():
    e, bk = TV.maker_engine()
    assert e.open_lot(SL, 'BTCUSDT', 'LONG', SG, None, e.equity())
    rec = next(iter(e.state['resting_entries'].values())); cid = rec['cid']
    e.trade.get_order = lambda s, c: None
    TV.age(e, 25); e.manage(e.trade.marks())
    assert not e.state['resting_entries']
    assert [u['cid'] for u in e.state['unconfirmed_entries'].values()] == [cid]


# ---------------------------------------------------------------- 4: grid sends fit the portfolio budget
def _reserve(e, usd, n=0, sym='ETHUSDT'):
    e.state['unconfirmed_entries'][f'UE|X{n}|{sym}|LONG|1'] = dict(symbol=sym, side='LONG', cid=f'zx{n}', qty=usd / 2.0,
                                                                     t=time.time(), prov=None, prov_qty=0.0, plan=dict(stop_dist=2.0))


def test_r4_the_grid_send_gate_refuses_an_order_over_the_portfolio_budget():
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    m = max(c['a'] for c in g['cells'] if c['k'] == 'L') - 0.05
    q = g['cells'][0]['q']
    gm._order_ok(g, q, m, 'LONG')                                             # fits while nothing else is open
    _reserve(e, 0.025 * _cap(e) - 1e-6)                                       # the budget is (almost) used elsewhere
    with pytest.raises(ValueError, match='open_risk_cap'): gm._order_ok(g, q, m, 'LONG')
    gm._order_ok(g, q, m)                                                     # no side: a reducing order is never risk-gated


def test_r4_grid_fills_never_take_total_stop_risk_over_the_cap():
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e); cap = 0.025 * _cap(e)
    _reserve(e, 0.02 * _cap(e))                                               # two ordinary 1 % positions' worth
    lines = sorted(c['a'] for c in g['cells'] if c['k'] == 'L')
    for px in reversed(lines): TG.tick(e, gm, px - 0.05)                       # walk down through every buy line
    tot = sum(e._lot_risk(l) for l in e.state['lots'].values()) + e._reserved_risk()
    assert tot <= cap + 1e-6, (tot, cap)


def test_r4_another_grids_in_flight_order_is_reserved():
    e, gm, _ = TG.mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = TG.grid_of(e)
    g['op'] = dict(id='op1', side='LONG', kind='open', cells=[0], qty=0.5, px=100.0, t=time.time(), full=False, cid='zc')
    want = 0.5 * (100.0 - g['stop_lo'])
    assert e._reserved_risk() == pytest.approx(want)
    assert e._reserved_risk(skip_op='op1') == 0.0
    g['op']['px'] = None
    assert e._reserved_risk() is None


def test_r4_a_non_finite_or_negative_planned_risk_is_unknown_not_zero():
    for bad in (float('nan'), float('inf'), -1.0):
        assert E.Engine._plan_risk(dict(risk_usd=bad), 1.0) is None
