"""Grid / COMBO tests: pure grid math, fee-positive spacing, worst case, the backtest simulator, and the live
GridManager on a real Engine with a fake Binance (mark moves drive fills, restart persistence, lost answers).
Run:  python -m pytest -q tests/test_grid.py"""
import os, sys, tempfile
import numpy as np, pandas as pd, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['LOCALAPPDATA'] = os.environ.get('LOCALAPPDATA') if 'zb_test_' in os.environ.get('LOCALAPPDATA', '') else tempfile.mkdtemp()
import binance_client as BC
import engine as E
import backtest as B
import grid as G


# ------------------------------------------------------------------ fake Binance (same shape as tests/test_safety.py)
class FakeX:
    def __init__(self, *a, **k):
        self.pos, self.stops, self.n, self.calls = {}, {}, 0, []
        self.mark = {'BTCUSDT': 100.0, 'ETHUSDT': 50.0}
        self.fail = set(); self.balance = 5000.0; self.offset = 0
    def _f(self, name):
        self.calls.append(name)
        if name in self.fail: raise BC.BinanceError(-1000, f'injected {name} failure')
    def sync_time(self): pass
    def exchange_info(self):
        return {'symbols': [{'symbol': s, 'contractType': 'PERPETUAL', 'status': 'TRADING', 'filters': [
            {'filterType': 'MARKET_LOT_SIZE', 'stepSize': '0.001', 'minQty': '0.001'}, {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
            {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]} for s in self.mark]}
    def marks(self): return dict(self.mark)
    def account(self): return {'totalMarginBalance': str(self.balance), 'totalUnrealizedProfit': '0'}
    def positions(self): self._f('positions'); return {k: v for k, v in self.pos.items() if v > 1e-12}
    def hedge_mode(self): return True
    def set_hedge_mode(self, on=True): pass
    def set_margin_type(self, *a): self._f('margin')
    def leverage_max(self, s): return 125
    def set_leverage(self, s, lev): self._f('leverage')
    def open(self, s, ps, q):
        self._f('open'); self.pos[(s, ps)] = round(self.pos.get((s, ps), 0) + float(q), 9); return {'avgPrice': str(self.mark[s]), 'executedQty': q}
    def close(self, s, ps, q):
        self._f('close'); self.pos[(s, ps)] = round(self.pos.get((s, ps), 0) - float(q), 9); return {'avgPrice': str(self.mark[s])}
    def stop(self, s, ps, q, p):
        self._f('stop'); self.n += 1; tag = f'o:{self.n}'; self.stops[tag] = (s, ps, float(q), float(p)); return tag
    def cancel(self, s, tag): self._f('cancel'); self.stops.pop(tag, None); return True
    def open_stop_tags(self, s): self._f('tags'); return {k for k, v in self.stops.items() if v[0] == s}
    def premium(self, s): return {'lastFundingRate': '0.0001'}


SLOT = dict(id='G1', enabled=True, auto=False, symbols=['BTCUSDT'], tf='4h', share=0.5, max_coins=1, mode='neutral',
            range_kind='pct', range_value=10, levels=10, spacing='geom', capital_frac=1.0, stop_out_pct=3.0, max_lev=3.0,
            trend_bars=3, cooldown_bars=0, need_range=True, max_worst_loss_pct=25)
DF = pd.DataFrame(dict(t=pd.date_range('2026-01-01', periods=50, freq='4h'), o=100.0, h=101.0, l=99.0, c=100.0, atr=2.0))


def mk(tmp=None, slot=None, fx=None):
    tmp = tmp or tempfile.mkdtemp()
    E.Futures = FakeX
    e = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), tmp)
    if fx is not None: e.trade = fx
    e.data = e.trade
    e.S.update(dict(UNIVERSE=['BTCUSDT', 'ETHUSDT'], SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True}, CAPITAL_CAP=500.0,
                    GRID_SLOTS=[dict(slot or SLOT)]))
    e.connect(); e.marks = e.trade.marks()
    gm = e.grids                      # the engine's own manager (integrated in engine.manage / cycle)
    gm.ranging = True
    gm.range_now = lambda sym, tf: (gm.ranging, DF.assign(t=DF.t + pd.Timedelta(hours=4 * gm.__dict__.setdefault('bar', 0))))
    return e, gm, tmp


def tick(e, gm, px, sym='BTCUSDT'):
    """What engine.manage does every 8 s once integrated: lot management + reconcile, then the grid step."""
    e.trade.mark[sym] = px
    m = e.trade.marks()
    e.manage(m)                        # engine.manage now runs the grid step itself


def grid_of(e): return next(iter(e.state['grids'].values()))
def lot_of(e, side): return next((l for l in e.state['lots'].values() if l['side'] == side), None)


def consistent(e):
    """Engine lots == exchange positions == grid bookkeeping; every lot has exactly one stop sized to it."""
    for (sym, side), q in e.trade.pos.items():
        lq = sum(l['qty'] for l in e.state['lots'].values() if l['symbol'] == sym and l['side'] == side)
        assert abs(q - lq) < 1e-6, (sym, side, q, lq)
    for g in e.state['grids'].values():
        for kind, side in (('L', 'LONG'), ('S', 'SHORT')):
            lot = lot_of(e, side)
            fq = sum(c['q'] for c in g['cells'] if c['k'] == kind and c['f'])
            assert abs((lot['qty'] if lot else 0) - fq) < 1e-6, (side, fq, lot and lot['qty'])
    for l in e.state['lots'].values():
        st = [v for v in e.trade.stops.values() if v[0] == l['symbol'] and v[1] == l['side']]
        assert len(st) == 1 and abs(st[0][2] - l['qty']) < 1e-9 and abs(st[0][3] - l['stop']) < 1e-9


# ------------------------------------------------------------------ pure grid math
def test_build_grid_lines_kinds_and_size():
    cfg = G.clean_cfg(dict(mode='neutral', range_kind='pct', range_value=10, levels=10, spacing='geom', capital_frac=2.0, max_lev=3))
    g = G.build_grid(100.0, 2.0, cfg, capital=1000.0)
    r = np.array(g['lines'][1:]) / np.array(g['lines'][:-1])
    assert len(g['lines']) == 11 and np.allclose(r, r[0]) and abs(g['lo'] - 90) < 1e-9 and abs(g['hi'] - 110) < 1e-9
    assert all(c['k'] == 'L' for c in g['cells'] if c['b'] <= 100) and all(c['k'] == 'S' for c in g['cells'] if c['a'] >= 100)
    assert not any(c['init'] for c in g['cells'])                                   # neutral starts flat
    for kind in 'LS':                                                               # one side fully filled = capital x capital_frac
        n = sum(c['q'] * (c['a'] if kind == 'L' else c['b']) for c in g['cells'] if c['k'] == kind)
        assert n <= 2000 + 1e-6
    assert max(sum(c['q'] * (c['a'] if c['k'] == 'L' else c['b']) for c in g['cells'] if c['k'] == k) for k in 'LS') == pytest.approx(2000)
    ga = G.build_grid(100.0, 2.0, dict(cfg, spacing='arith', mode='long', range_kind='atr', range_value=5), capital=1000.0)
    d = np.diff(ga['lines']); assert np.allclose(d, d[0]) and abs(ga['lo'] - 90) < 1e-9
    assert all(c['init'] == ((c['a'] + c['b']) / 2 >= 100) for c in ga['cells'])     # long mode holds the cells above the start
    with pytest.raises(ValueError, match='inside the range'): G.build_grid(100.0, 2.0, dict(cfg, range_kind='lookback', range_value=50), 1000, lo_hi=(101, 120))


def test_worst_case_loss_by_hand():
    cfg = G.clean_cfg(dict(mode='long', range_kind='pct', range_value=10, levels=4, spacing='arith', capital_frac=1, stop_out_pct=5))
    g = G.build_grid(100.0, 1.0, cfg, capital=1000.0)
    m = G.risk_metrics(g)
    stop = 90 * 0.95
    ent = [c['a'] if not c['init'] else 100.0 for c in g['cells']]
    q = [c['q'] for c in g['cells']]
    notl = sum(a * b for a, b in zip(q, ent)); Q = sum(q)
    hand = notl - Q * stop + (notl + Q * stop) * (G.FEE + G.SLIP)
    assert m['worst_loss_usd'] == pytest.approx(hand, rel=1e-4) and m['worst_loss_pct'] == pytest.approx(hand / 10, rel=1e-3)
    assert m['eff_leverage'] == pytest.approx(1.0) and m['max_notional'] == pytest.approx(1000.0)
    assert m['liq_distance_pct'] == 100.0                                           # 1x: no liquidation price
    g5 = G.build_grid(100.0, 1.0, dict(cfg, capital_frac=8, max_lev=10), capital=1000.0); m5 = G.risk_metrics(g5)
    assert 0 < m5['liq_price'] < stop and m5['liq_distance_pct'] > 0                 # stop fires before liquidation
    assert m5['worst_loss_usd'] == pytest.approx(8 * m['worst_loss_usd'], rel=1e-3)


def test_fee_positive_spacing_and_validation():
    c_ = G.FEE + G.SLIP
    _, m = G.validate_cfg(dict(range_kind='pct', range_value=10, levels=10))
    assert m['min_fee_positive_spacing_pct'] == pytest.approx(((1 + c_) / (1 - c_) - 1) * 100, abs=1e-4)
    assert m['profit_per_cycle_after_fees'] > 0 and m['spacing_pct'] > 2 * m['min_fee_positive_spacing_pct']
    lvl = m['min_fee_positive_spacing_pct'] / 100                                   # a cell exactly at that spacing earns ~0
    a, b = 100.0, 100.0 * (1 + lvl); assert abs((b - a) - (a + b) * c_) < 1e-4
    with pytest.raises(ValueError, match='does not cover fees'): G.validate_cfg(dict(range_kind='pct', range_value=1, levels=40))
    with pytest.raises(ValueError, match='fees would eat'): G.validate_cfg(dict(range_kind='pct', range_value=2, levels=20))
    with pytest.raises(ValueError, match='worst case'): G.validate_cfg(dict(range_kind='pct', range_value=20, capital_frac=3, stop_out_pct=10))
    with pytest.raises(ValueError, match='above the grid'): G.validate_cfg(dict(capital_frac=5, max_lev=3))
    with pytest.raises(ValueError, match='levels'): G.validate_cfg(dict(levels=2))
    s = G.validate_slot(dict(SLOT, levels=12), 0); assert s['id'] == 'G1' and s['levels'] == 12 and s['share'] == 0.5
    with pytest.raises(ValueError, match='already used'): G.validate_slot(dict(SLOT, id='MOM'), 0, strategy_ids=('MOM',))


def test_triggers():
    g = G.build_grid(100.0, 2.0, G.clean_cfg(dict(range_kind='pct', range_value=10, levels=10, stop_out_pct=3)), 1000)
    t = G.triggers(g, 97.5)
    assert [g['cells'][j]['a'] >= 97.5 for j in t['L']['add']] == [True] * len(t['L']['add']) and t['L']['add'] and not t['S']['add']
    assert G.triggers(g, 87.0)['stop'] == 'lo' and G.triggers(g, 114.0)['stop'] == 'hi' and G.triggers(g, 100.0)['stop'] is None


# ------------------------------------------------------------------ simulator
def _book(close, n0=0):
    t = pd.date_range('2025-01-01', periods=len(close), freq='4h')
    o = np.r_[close[0], close[:-1]]
    return B.Book({'BTCUSDT': pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, close) * 1.002, l=np.minimum(o, close) * 0.998, c=close, v=1.0))})


def test_sine_range_makes_money():
    n = 900
    bk = _book(100 + 3 * np.sin(np.arange(n) / 6.0))
    tr, cv = G.run_grid(bk, ['BTCUSDT'], dict(mode='neutral', range_kind='pct', range_value=5, levels=10, need_range=False), 500)
    assert cv.iloc[-1] > 540 and cv.attrs['cycles'] > 50 and cv.attrs['stops'] == 0 and cv.attrs['cycle_pnl'] > 0
    assert set(tr.columns) >= {'sleeve', 'sym', 'side', 'i_in', 'i_out', 'pnl', 'R', 'why'} and (cv.index == bk.t.iloc[220:]).all()
    tr2, cv2 = G.run_combo(bk, ['BTCUSDT'], dict(need_uptrend=False, dip_atr=1.0, max_coins=1), 500)
    assert cv2.attrs['baskets'] > 0 and cv2.attrs['tps'] > 0 and len(cv2) == len(cv)


def test_trend_through_range_stops_at_computed_worst_case():
    n = 700
    close = np.r_[np.full(300, 100.0), 100 * np.exp(np.cumsum(np.full(n - 300, -0.004)))]
    cfg = dict(mode='long', range_kind='pct', range_value=5, levels=10, need_range=False, stop_out_pct=2, cooldown_bars=100)
    tr, cv = G.run_grid(_book(close), ['BTCUSDT'], cfg, 500)
    first = tr.iloc[0]
    worst = G.risk_metrics(G.build_grid(100.0, 1.0, G.clean_cfg(cfg), 500.0))['worst_loss_usd']
    assert first.why == 'stop' and first.pnl < 0
    assert -first.pnl == pytest.approx(worst, rel=0.08)                             # computed worst case (+ funding while held)
    assert first.R == pytest.approx(-1.0, abs=0.08) and cv.attrs['liquidations'] == 0
    assert all(tr[tr.why == 'stop'].R.between(-1.1, -0.95))


# ------------------------------------------------------------------ live GridManager on a real Engine
def test_live_grid_fills_cycles_and_flips():
    e, gm, _ = mk()
    with e.lock: st = gm.start('G1', 'BTCUSDT')
    g = grid_of(e)
    assert st['status'] == 'active' and not e.state['lots'] and st['cells_long'] == 5 and st['cells_short'] == 5
    top_l = max(c['a'] for c in g['cells'] if c['k'] == 'L')
    tick(e, gm, top_l - 0.05)                                                      # first buy: 0 -> >0 creates the lot
    L = lot_of(e, 'LONG'); assert L and L['key_strategy'] == 'grid' and L['stop'] == g['stop_lo'] and L['sleeve'] == 'G1'
    consistent(e)
    lines = sorted(c['a'] for c in g['cells'] if c['k'] == 'L')
    tick(e, gm, lines[-2] - 0.05)                                                  # next line down: add + stop resized
    assert sum(c['f'] for c in g['cells']) == 2; consistent(e)
    q2 = L['qty']
    tick(e, gm, lines[-1] + 0.05)                                                  # back up through the lower cell's top: one cycle
    assert g['cycles'] == 1 and g['cycle_pnl'] > 0 and L['qty'] < q2; consistent(e)
    tick(e, gm, 100.0); tick(e, gm, min(c['b'] for c in g['cells'] if c['k'] == 'S') + 0.05)   # long flat, first short
    assert lot_of(e, 'LONG') is None and lot_of(e, 'SHORT') is not None and g['cycles'] == 2
    assert e.history[-1]['exit_reason'] == 'grid_flat' and e.history[-1]['pnl'] > 0
    consistent(e)
    s = gm.status(); assert s['grids'][0]['filled_short'] == 1 and s['grids'][0]['inv_long'] == 0.0


def test_restart_persistence():
    e, gm, tmp = mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    lines = sorted(c['a'] for c in grid_of(e)['cells'] if c['k'] == 'L')
    tick(e, gm, lines[-2] - 0.05)
    before = grid_of(e)
    e2, gm2, _ = mk(tmp, fx=e.trade)                                               # app restarted, same exchange
    g2 = grid_of(e2)
    assert g2['cells'] == before['cells'] and g2['lots'] == before['lots'] and g2['lots']['LONG'] in e2.state['lots']
    tick(e2, gm2, lines[-1] + 0.05)
    assert g2['cycles'] == 1; consistent(e2)
    with pytest.raises(ValueError, match='already running'):
        with e2.lock: gm2.start('G1', 'BTCUSDT')


def ambiguous_once(e, name, fill=True):
    orig = getattr(e.trade, name)
    def f(*a, **k):
        setattr(e.trade, name, orig)
        if fill: orig(*a, **k)
        raise BC.AmbiguousOrder('response lost', 'c:zbtest')
    setattr(e.trade, name, f)


@pytest.mark.parametrize('fill', [True, False])
def test_lost_answers_never_double_or_guess(fill):
    e, gm, _ = mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = grid_of(e)
    lines = sorted(c['a'] for c in g['cells'] if c['k'] == 'L')
    ambiguous_once(e, 'open', fill)                                                # the very first order (lot creation)
    tick(e, gm, lines[-1] - 0.05)
    assert g['op'] and g['op']['kind'] == 'open' and not e.state['lots']
    for _ in range(3): tick(e, gm, lines[-1] - 0.05)
    if not fill:                                                                   # never filled: dropped after 20 s, then re-sent
        g['op'] and g['op'].update(t=0); tick(e, gm, lines[-1] - 0.05)
    assert lot_of(e, 'LONG') and sum(c['f'] for c in g['cells']) == 1 and not e.untracked; consistent(e)
    ambiguous_once(e, 'open', fill)                                                # an add
    tick(e, gm, lines[-2] - 0.05)
    assert lot_of(e, 'LONG').get('pending') and g['op']['kind'] == 'add'
    for _ in range(3): tick(e, gm, lines[-2] - 0.05)
    if not fill: lot_of(e, 'LONG').get('pending', {}).update(t=0); [tick(e, gm, lines[-2] - 0.05) for _ in range(2)]
    assert sum(c['f'] for c in g['cells']) == 2 and not g['op']; consistent(e)
    ambiguous_once(e, 'close', fill)                                               # a take-profit
    tick(e, gm, lines[-1] + 0.05)
    for _ in range(3): tick(e, gm, lines[-1] + 0.05)
    if not fill: lot_of(e, 'LONG').get('pending', {}).update(t=0); [tick(e, gm, lines[-1] + 0.05) for _ in range(2)]
    assert g['cycles'] == 1 and sum(c['f'] for c in g['cells']) == 1 and not e.untracked; consistent(e)


def test_exchange_stop_hit_stops_the_grid():
    e, gm, _ = mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = grid_of(e)
    tick(e, gm, min(c['a'] for c in g['cells']) - 0.01)                            # all long cells filled
    assert sum(c['f'] for c in g['cells']) == 5; consistent(e)
    e.trade.pos[('BTCUSDT', 'LONG')] = 0; e.trade.stops.clear()                    # Binance stop fired
    tick(e, gm, g['stop_lo'] - 0.1)
    assert not e.state['grids'] and not e.state['lots'] and e.state['grid_history'][-1]['why'] == 'stop'
    assert e.history[-1]['exit_reason'] == 'stop'


def test_range_exit_and_trend_stop_close_everything():
    e, gm, _ = mk()
    with e.lock: gm.start('G1', 'BTCUSDT')
    g = grid_of(e)
    tick(e, gm, max(c['b'] for c in g['cells']) - 0.01)                            # shorts filled
    assert lot_of(e, 'SHORT'); tick(e, gm, g['stop_hi'] + 0.5)                     # beyond the hard stop on marks
    assert not e.state['grids'] and not e.trade.positions() and 'above' in e.state['grid_history'][-1]['why']
    tick(e, gm, 100.0)                                                             # back in the old range: a fresh grid around 100
    with e.lock: gm.start('G1', 'BTCUSDT')
    tick(e, gm, 96.0); assert lot_of(e, 'LONG')
    gm.ranging = False
    for b in range(1, 4):
        gm.bar = b
        with e.lock: gm.on_cycle('4h')
    tick(e, gm, 96.0)
    assert not e.state['grids'] and not e.trade.positions() and e.state['grid_history'][-1]['why'] == 'trend'


def test_start_gates():
    e, gm, _ = mk()
    e.S['ENTRIES_PAUSED'] = True
    with pytest.raises(ValueError, match='paused'): gm.start('G1', 'BTCUSDT')
    e.S['ENTRIES_PAUSED'] = False; gm.ranging = False
    with pytest.raises(ValueError, match='not in a range'): gm.start('G1', 'BTCUSDT')
    gm.ranging = True
    with pytest.raises(ValueError, match='worst case'): gm.start(dict(SLOT, capital_frac=3, range_value=20, stop_out_pct=10), 'BTCUSDT')
    with pytest.raises(ValueError, match='minimum order'): gm.start(dict(SLOT, share=0.01, levels=40, range_value=30), 'BTCUSDT')
    e.state['halted'] = True
    with pytest.raises(ValueError, match='halt'): gm.start('G1', 'BTCUSDT')
    e.state['halted'] = False
    with e.lock: gm.start('G1', 'BTCUSDT')
    e.S['ENTRIES_PAUSED'] = True                                                   # paused: no new inventory, take-profits still run
    tick(e, gm, 96.0); assert not e.state['lots']


def test_auto_start_on_cycle():
    e, gm, _ = mk(slot=dict(SLOT, auto=True))
    with e.lock: gm.on_cycle('4h')
    assert 'G1|BTCUSDT' in e.state['grids']
