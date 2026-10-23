"""SCALP-EXEC-15M-01 (EDGE-01A): exact runtime exp_scalp / range_scalp on a 1m path + cost re-pricing.

Contract: ZackBot2 #186 comment 6353256443. Pins: the replay hooks are decision-neutral when they reproduce the legacy
path (parity), the 1m walk is stop first with levels as their own points, a minute open is a gap point, missing 1m data
falls back to a labelled stop-first path, inconsistent 1m data refuses, funding is signed and windowed, the sealed cutoff
refuses, and re-pricing with the legacy cost constants reproduces the engine's own price P&L.
"""
import copy
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'tools', 'research')]

import backtest as B                                      # noqa: E402
import costs as C                                         # noqa: E402
import scalp_exec_15m as X                                # noqa: E402
import strategies as S                                    # noqa: E402

SYM = 'SOLUSDT'
T0 = 260


def _minutes(n15, seed, start='2025-01-06'):
    rng = np.random.default_rng(seed)
    n = n15 * 15
    t = pd.date_range(start, periods=n, freq='1min')
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.r_[100.0, c[:-1]]
    w = np.abs(rng.normal(0, 0.0006, n)) * c
    h, l = np.maximum(o, c) + w, np.minimum(o, c) - w
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=rng.uniform(50, 150, n)))


def _agg(m):
    g = m.groupby(m.t.dt.floor('15min'))
    return pd.DataFrame(dict(t=g.t.first().dt.floor('15min').values, o=g.o.first().values, h=g.h.max().values,
                             l=g.l.min().values, c=g.c.last().values, v=g.v.sum().values))


def _sleeve(key='exp_scalp', side='both', mgmt=None):
    import engine as E
    return [E.sleeve('SC', key, 1.0, 0.01, 2, [SYM], sides=side, mgmt=mgmt or {}, tf='15m')]


# ------------------------------------------------------------------ the 1m path (pure)
def test_long_minute_walks_adverse_first_and_stops_at_levels():
    bar = X.Bar((0, 100.0, 103.0, 97.0, 101.0))
    mins = [X.Bar((k * 60_000, 100.0, 100.5, 99.5, 100.0)) for k in range(15)]
    mins[3] = X.Bar((3 * 60_000, 100.0, 103.0, 97.0, 100.0))        # one minute spans the stop (98) and the target (102)
    mins[-1] = X.Bar((14 * 60_000, 100.0, 101.0, 99.5, 101.0))
    pts, label = X.bar_path(bar, mins, {1}, ([98.0], [102.0]))
    px = [p for p, _o, _t in pts]
    assert label == X.SAME_MINUTE
    i98, i102 = px.index(98.0), px.index(102.0)
    assert i98 < i102                                               # stop reached before the target inside the minute
    assert px[i98 + 1] == 97.0 and px[i102 + 1] == 103.0             # the level is its own point, then the extreme


def test_short_mirror_is_adverse_first():
    bar = X.Bar((0, 100.0, 103.0, 97.0, 99.0))
    mins = [X.Bar((k * 60_000, 100.0, 100.4, 99.6, 100.0)) for k in range(15)]
    mins[0] = X.Bar((0, 100.0, 103.0, 97.0, 100.0)); mins[-1] = X.Bar((14 * 60_000, 100.0, 100.0, 99.0, 99.0))
    pts, label = X.bar_path(bar, mins, {-1}, ([102.0], [98.0]))
    px = [p for p, _o, _t in pts]
    assert label == X.SAME_MINUTE and px.index(102.0) < px.index(98.0)


def test_minute_open_is_a_gap_point():
    bar = X.Bar((0, 100.0, 100.5, 95.0, 95.5))
    mins = [X.Bar((k * 60_000, 100.0, 100.5, 99.8, 100.0)) for k in range(15)]
    mins[5] = X.Bar((5 * 60_000, 96.0, 96.2, 95.0, 95.5))            # gaps down through a 98 stop at its open
    for k in range(6, 15): mins[k] = X.Bar((k * 60_000, 95.5, 95.6, 95.2, 95.5))
    pts, _ = X.bar_path(bar, mins, {1}, ([98.0], []))
    gaps = [(p, t) for p, o, t in pts if o]
    assert (96.0, 5 * 60_000) in gaps
    k = [p for p, _o, _t in pts].index(96.0)
    assert all(p >= 98.0 for p, _o, _t in pts[:k])                  # nothing before the gap crosses the stop


def test_missing_minute_falls_back_to_stop_first_and_labels_unresolved():
    bar = X.Bar((0, 100.0, 103.0, 97.0, 101.0))
    mins = [X.Bar((k * 60_000, 100.0, 100.5, 99.5, 100.0)) for k in range(14)]   # 14 of 15
    pts, label = X.bar_path(bar, mins, {1}, ([98.0], [102.0]))
    px = [p for p, _o, _t in pts]
    assert label == X.NO_1M and px.index(98.0) < px.index(102.0)
    _, label1 = X.bar_path(bar, mins, {1}, ([98.0], []))
    assert label1 == X.ONE_LEVEL


def test_inconsistent_minutes_refuse():
    bar = X.Bar((0, 100.0, 103.0, 97.0, 101.0))
    mins = [X.Bar((k * 60_000, 100.0, 100.5, 99.5, 100.0)) for k in range(15)]
    mins[-1] = X.Bar((14 * 60_000, 100.0, 101.0, 99.5, 101.0))
    with pytest.raises(X.ScalpExecError, match='inconsistent'):
        X.bar_path(bar, mins, {1}, ([98.0], [102.0]))                # 1m extremes never reach the 15m 103 / 97


def test_resolved_when_stop_and_target_fall_in_different_minutes():
    bar = X.Bar((0, 100.0, 103.0, 97.0, 101.0))
    mins = [X.Bar((k * 60_000, 100.0, 100.5, 99.5, 100.0)) for k in range(15)]
    mins[2] = X.Bar((2 * 60_000, 100.0, 103.0, 99.5, 100.0)); mins[9] = X.Bar((9 * 60_000, 100.0, 100.5, 97.0, 100.0))
    mins[-1] = X.Bar((14 * 60_000, 100.0, 101.0, 99.5, 101.0))
    pts, label = X.bar_path(bar, mins, {1}, ([98.0], [102.0]))
    px = [p for p, _o, _t in pts]
    assert label == X.RES_1M and px.index(102.0) < px.index(98.0)    # the target minute comes first in time


def test_lot_levels_follow_the_engine_fields():
    lot = dict(side='LONG', stop=95.0, e0=100.0, R=5.0, tp1=False, mgmt=dict(tp1_r=0.75, tp_r=1.5))
    assert X.lot_levels(lot) == ([95.0], [103.75, 107.5])
    lot.update(tp1=True, side='SHORT', stop=105.0)
    assert X.lot_levels(lot) == ([105.0], [92.5])
    lot['mgmt'] = dict(tp_r=1.5, runner=True)
    assert X.lot_levels(lot) == ([105.0], [])


# ------------------------------------------------------------------ guards
def test_sealed_cutoff_refuses_15m_1m_and_funding():
    m = _minutes(40, 1, start='2026-09-27T20:00')
    with pytest.raises(X.ScalpExecError, match='sealed cutoff'):
        X.check_inputs({SYM: _agg(m)}, {}, {})
    ok = _minutes(40, 1, start='2026-09-27T00:00')
    assert X.check_inputs({SYM: _agg(ok)}, {SYM: ok}, {}) == {}
    with pytest.raises(X.ScalpExecError, match='sealed cutoff'):
        X.check_inputs({SYM: _agg(ok)}, {}, {SYM: [(X.CUTOFF_MS, 0.0001, 100.0)]})


def test_cutoff_equals_the_shared_research_cutoff():
    import run_vwaprev_candidate as VC
    assert X.SEALED_CUTOFF == VC.SEALED_CUTOFF


def test_missing_15m_candle_excludes_the_symbol():
    d = _agg(_minutes(40, 2)).drop(index=7)
    assert X.check_inputs({SYM: d}, {}, {}) == {SYM: 'missing 15m candle(s)'}


def test_sleeves_outside_the_two_keys_or_tf_or_with_resting_tp_refuse():
    import engine as E
    with pytest.raises(X.ScalpExecError, match='not one of'):
        X.check_sleeves([E.sleeve('A', 'exp_trend', 1, .01, 1, [SYM], tf='15m')])
    with pytest.raises(X.ScalpExecError, match='timeframe'):
        X.check_sleeves([E.sleeve('A', 'exp_scalp', 1, .01, 1, [SYM], tf='4h')])
    with pytest.raises(X.ScalpExecError, match='resting'):
        X.check_sleeves(_sleeve(mgmt=dict(resting_tp=True, tp_r=1.5)))


# ------------------------------------------------------------------ costs
def _trade(side='LONG', funding_close_ms=None):
    e = dict(t_ms=20 * 900_000, sym=SYM, ps=side, act='open', kind='market', qty=2.0, ref=100.0)
    c = dict(t_ms=funding_close_ms or 24 * 900_000, sym=SYM, ps=side, act='close', kind='stop', qty=2.0,
             ref=95.0 if side == 'LONG' else 105.0)
    return dict(sym=SYM, ps=side, entry=e, closes=[c], risk_usd=10.0)


def _bars(n=30):
    return [X.Bar((k * 900_000, 100.0, 100.5, 99.5, 100.0)) for k in range(n)]


def test_funding_sign_and_window():
    base = C.STRESS['base']
    rows = [(19 * 900_000, 0.001, 100.0), (20 * 900_000, 0.001, 100.0), (22 * 900_000, 0.001, 100.0), (24 * 900_000, 0.001, 100.0)]
    kw = dict(row=C.CRYPTO, slip_c=1e-9, bars15=_bars())
    lg = X.price_trade(_trade('LONG'), base, funding_rows=rows, **kw)
    sh = X.price_trade(_trade('SHORT'), base, funding_rows=rows, **kw)
    assert lg['funding'] == pytest.approx(2 * 0.001 * 2.0 * 100.0)   # T = entry counts, T = exit does not
    assert sh['funding'] == pytest.approx(-lg['funding'])            # shorts receive a positive rate
    none = X.price_trade(_trade('LONG'), base, funding_rows=None, **kw)
    assert none['complete'] is False and none['funding'] == 0.0


def test_stress_rows_cost_more_and_gate_row_is_registered():
    kw = dict(row=C.CRYPTO, slip_c=0.05, bars15=_bars(), funding_rows=[])
    b = X.price_trade(_trade(), C.STRESS['base'], **kw)
    s = X.price_trade(_trade(), X.SCENARIOS['edge01_x1_5'], **kw)
    assert s['net'] < b['net'] and s['fees'] == pytest.approx(1.5 * b['fees'], rel=1e-3)
    assert set(C.STRESS) < set(X.SCENARIOS) and 'edge01_x1_5' not in C.STRESS
    assert b['gross_r'] == pytest.approx(-1.0)                       # (95 - 100) x 2 / 10 at the reference prices


def test_fills_before_warm_up_refuse():
    tr = _trade(); tr['entry']['t_ms'] = 10 * 900_000                  # only 10 closed candles before the entry
    with pytest.raises(X.ScalpExecError, match='warm-up'):
        X.price_trade(tr, C.STRESS['base'], row=C.CRYPTO, slip_c=0.05, bars15=_bars(), funding_rows=[])


# ------------------------------------------------------------------ engine runs
def test_hook_off_and_legacy_points_are_decision_identical():
    """Parity (realism off): a path_fn that returns exactly the legacy interpolated path changes no decision."""
    from replay import run_replay
    m = _minutes(420, 7)
    raw = {SYM: _agg(m)}
    steps = 6

    def legacy(j, held, lots):
        d = raw[SYM]
        if SYM not in held: return {}
        o, h, l, c = d.o[j], d.h[j], d.l[j], d.c[j]
        pth = B.path_points(o, h, l, c, next(iter(held[SYM]))) if len(held[SYM]) == 1 else B.path_points(o, h, l, c, 1)
        pts, a = [], o
        for tgt in pth[1:]:
            pts += [(a + (tgt - a) * k / steps, False, 0) for k in range(1, steps + 1)]; a = tgt
        return {SYM: pts}

    sl = _sleeve()
    a = run_replay(copy.deepcopy(raw), copy.deepcopy(sl), T0, tf_sec=900, steps=steps, compare=False)
    b = run_replay(copy.deepcopy(raw), copy.deepcopy(sl), T0, tf_sec=900, steps=steps, compare=False, path_fn=legacy)
    key = lambda h: [(x['symbol'], x['side'], x['opened'], x['closed'], x['exit_reason'], round(x['pnl'], 9)) for x in h]
    assert len(a['history']) >= 3
    assert key(a['history']) == key(b['history'])


def test_engine_on_the_1m_path_fills_stops_at_the_level_and_reprices_to_engine_pnl():
    """Real exp_scalp on a 1m random walk: every stop fill is AT the engine's stop (or a minute open past it), and
    re-pricing at the legacy constants (2 bps floor slip = backtest SLIP, taker = backtest FEE, no funding) reproduces
    the engine's own price P&L when the same run is made with the legacy costs on."""
    from replay import run_replay
    m = _minutes(420, 11)
    raw = {SYM: _agg(m)}
    labels, fills = [], []
    out = run_replay(copy.deepcopy(raw), _sleeve(), T0, tf_sec=900, compare=False, costs_off=True, fill_log=fills,
                     path_fn=X.make_path_fn(raw, {SYM: m}, labels)[0])
    trades = X.trades_of(fills, out['history'])
    assert len(trades) >= 3 and labels
    assert {x[2] for x in labels} <= {X.ONE_LEVEL, X.RES_1M, X.SAME_MINUTE, X.TWO_SIDED}     # no NO-1M: data complete
    for tr in trades:
        assert tr['engine_gross'] == pytest.approx(sum((1 if tr['ps'] == 'LONG' else -1) * (f['ref'] - tr['entry']['ref']) * f['qty']
                                                     for f in tr["closes"]), abs=1e-4)   # history rounds to 4 dp
    leg = C.Scenario('legacy_repro')                                 # actual funding over no rows = 0
    B15 = X.bars_of(raw[SYM])
    for tr in trades:
        p = X.price_trade(tr, leg, row=C.CRYPTO, slip_c=1e-9, bars15=B15, funding_rows=[])
        sd = 1 if tr['ps'] == 'LONG' else -1
        e = tr['entry']['ref'] * (1 + sd * B.SLIP)
        want = sum(sd * (f['ref'] * (1 - sd * B.SLIP) - e) * f['qty'] - f['qty'] * f['ref'] * (1 - sd * B.SLIP) * B.FEE
                   for f in tr['closes']) - tr['entry']['qty'] * e * B.FEE
        assert p['net'] == pytest.approx(want, rel=1e-9, abs=1e-9)


def test_runtime_files_are_untouched():
    base = subprocess.run(['git', 'merge-base', 'HEAD', 'origin/master'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not base:
        pytest.skip('no origin/master in this checkout')
    diff = subprocess.run(['git', 'diff', '--name-only', base, '--', 'engine.py', 'strategies.py', 'app.py', 'backtest.py'],
                          cwd=ROOT, capture_output=True, text=True).stdout.strip()
    assert diff == ''


# ------------------------------------------------------------------ Cowork #186 A2 / A3 / A4 and engine-level parity (b)
def _flat_minutes(n15, start='2025-01-06'):
    t = pd.date_range(start, periods=n15 * 15, freq='1min')
    return pd.DataFrame(dict(t=t, o=100.0, h=100.5, l=99.5, c=100.0, v=100.0))   # 15m true range 1 -> ATR 1


def _set(m, at, o, h, l, c):
    k = m.index[m.t == pd.Timestamp(at)][0]
    m.loc[k, ['o', 'h', 'l', 'c']] = [o, h, l, c]


def _run_injected(m, sig_bar, side=1, mgmt=None):
    """Engine on the 1m path with one injected entry signal on 15m candle `sig_bar` (entry at the next candle's open)."""
    import engine as E
    from replay import run_replay
    raw = {SYM: _agg(m)}
    sig_t = raw[SYM].t[sig_bar]
    real = S.signals

    def fake(key, d, ctx, params=None, mask_sides=True):
        out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
        out['le' if side == 1 else 'se'][:] = (pd.to_datetime(d.t) == sig_t).values
        return out
    S.signals = fake
    labels, fills = [], []
    try:
        sl = [E.sleeve('SC', 'exp_scalp', 1.0, 0.01, 1, [SYM], sides='long' if side == 1 else 'short', mgmt=mgmt or {},
                       tf='15m')]
        out = run_replay(copy.deepcopy(raw), sl, T0, tf_sec=900, compare=False, costs_off=True, fill_log=fills,
                         path_fn=X.make_path_fn(raw, {SYM: m}, labels)[0])
    finally:
        S.signals = real
    return raw, out, fills, labels


def _entry_open(raw, sig_bar):
    return int(raw[SYM].t[sig_bar + 1].value // 10 ** 6)


def test_a3_a_stop_touched_in_the_entry_minute_fills_there():
    m = _flat_minutes(300)
    sig = 280
    raw0 = _agg(m)
    t_e = raw0.t[sig + 1]
    _set(m, t_e, 100.0, 100.2, 98.5, 99.0)                          # entry minute dips through the 99.0 stop
    raw, out, fills, _ = _run_injected(m, sig)
    t0 = _entry_open(raw, sig)
    opens = [f for f in fills if f['act'] == 'open']
    stops = [f for f in fills if f['act'] == 'stop_set']
    closes = [f for f in fills if f['act'] == 'close']
    assert opens and opens[0]['t_ms'] == t0 and opens[0]['ref'] == 100.0
    assert closes[0]['kind'] == 'stop' and closes[0]['ref'] == pytest.approx(stops[0]['ref'])
    assert t0 <= closes[0]['t_ms'] < t0 + 60_000                    # inside the entry minute, not from minute 2
    assert out['history'][0]['exit_reason'] == 'stop'


def test_a2_later_minutes_of_a_candle_never_change_earlier_decisions():
    """Two runs identical up to minute 8 of a candle and different after it (a spike through the target): every fill
    and stop order before that minute is identical, so nothing in the walk reads the candle's own later high/low/close."""
    sig = 280
    a = _flat_minutes(300)
    b = a.copy()
    t_j = _agg(a).t[sig + 3]
    for k in range(8, 15):
        _set(b, t_j + pd.Timedelta(minutes=k), 100.0, 104.0, 99.9, 103.5)
    ra, oa, fa, _ = _run_injected(a, sig)
    rb, ob, fb, _ = _run_injected(b, sig)
    cut = int(t_j.value // 10 ** 6) + 8 * 60_000
    assert [f for f in fa if f['t_ms'] < cut] == [f for f in fb if f['t_ms'] < cut]
    assert any(f['act'] == 'close' and f['t_ms'] >= cut for f in fb)    # the spike does close the trade in run b ...
    assert not any(f['act'] == 'close' and f['t_ms'] < cut for f in fb)  # ... and only from that minute on


def test_engine_gap_through_stop_fills_at_the_minute_open():
    m = _flat_minutes(300)
    sig = 280
    t_e = _agg(m).t[sig + 1]
    _set(m, t_e + pd.Timedelta(minutes=4), 97.0, 97.2, 96.8, 97.0)  # opens 2 below the 99 stop
    for k in range(5, 15):
        _set(m, t_e + pd.Timedelta(minutes=k), 97.0, 97.2, 96.8, 97.0)
    raw, out, fills, _ = _run_injected(m, sig)
    c = [f for f in fills if f['act'] == 'close'][0]
    assert c['kind'] == 'gap' and c['ref'] == 97.0 and c['t_ms'] == int(t_e.value // 10 ** 6) + 4 * 60_000


def test_engine_both_touched_in_one_minute_takes_the_stop_and_labels_it():
    m = _flat_minutes(300)
    sig = 280
    t_e = _agg(m).t[sig + 1]
    _set(m, t_e + pd.Timedelta(minutes=3), 100.0, 103.0, 97.0, 100.0)   # spans the 99 stop and the 101.5 target
    raw, out, fills, labels = _run_injected(m, sig)
    c = [f for f in fills if f['act'] == 'close']
    assert len(c) == 1 and c[0]['kind'] == 'stop'
    assert (SYM, int(t_e.value // 10 ** 6), X.SAME_MINUTE) in labels


def test_a4_exp_scalp_signal_is_causal():
    """Changing only the LAST candle never changes the signal on any earlier candle."""
    d0 = _agg(_minutes(400, 21))
    d1 = d0.copy()
    d1.loc[d1.index[-1], ['h', 'c']] = [d1.h.iloc[-1] * 1.03, d1.c.iloc[-1] * 1.03]
    for key in X.KEYS:
        fn = S.STRATEGIES[key]['fn']                                  # the registry function the engine calls
        s0 = fn(S.indicators(d0.assign(qv=np.nan)), {}, {})
        s1 = fn(S.indicators(d1.assign(qv=np.nan)), {}, {})
        for k in ('le', 'se', 'lx', 'sx'):
            assert (np.asarray(s0[k])[:-1] == np.asarray(s1[k])[:-1]).all(), (key, k)


def test_risk_fill_would_is_reported_per_row():
    tr = _trade(); tr['stop0'] = 95.0
    b = X.price_trade(tr, C.STRESS['base'], row=C.CRYPTO, slip_c=1e-9, bars15=_bars(), funding_rows=[])
    assert b['risk_fill_would'] == 'ok'
    hot = C.Scenario('hot', slip_mult=1000.0)                         # 2 bps x 1000 = 2 % adverse on a 5 % stop
    assert X.price_trade(tr, hot, row=C.CRYPTO, slip_c=1e-9, bars15=_bars(), funding_rows=[])['risk_fill_would'] in ('trim', 'unwind')


# ------------------------------------------------------------------ Codex ruling 00ba8d0: latency, account path, frozen config
def _injected(m, sig_bar, latency_s=0, pricer=None, side=1):
    import engine as E
    raw = {SYM: _agg(m)}
    sig_t = raw[SYM].t[sig_bar]
    real = S.signals

    def fake(key, d, ctx, params=None, mask_sides=True):
        out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
        out['le' if side == 1 else 'se'][:] = (pd.to_datetime(d.t) == sig_t).values
        return out
    S.signals = fake
    try:
        sl = [E.sleeve('SC', 'exp_scalp', 1.0, 0.01, 1, [SYM], sides='long' if side == 1 else 'short', tf='15m')]
        out, fills, labels = X._engine(raw, {SYM: m}, sl, T0, start=500.0, rules=None, settings=None,
                                       latency_s=latency_s, pricer=pricer)
    finally:
        S.signals = real
    return raw, out, fills, labels


def test_latency_60s_enters_at_the_minute_1_open_and_still_manages_minute_0():
    m = _flat_minutes(300)
    sig = 280
    t_e = _agg(m).t[sig + 1]
    _set(m, t_e + pd.Timedelta(minutes=1), 100.4, 100.5, 99.5, 100.0)     # minute 1 opens at 100.4
    raw, out, fills, _ = _injected(m, sig, latency_s=60)
    op = [f for f in fills if f['act'] == 'open'][0]
    assert op['t_ms'] == int(t_e.value // 10 ** 6) + 60_000 and op['ref'] == 100.4
    # a lot already open: its stop touched in minute 0 of a later candle fills BEFORE the delayed cycle
    m2 = _flat_minutes(300)
    t_k = _agg(m2).t[sig + 3]
    _set(m2, t_k, 100.0, 100.2, 98.5, 99.0)
    _, _, f2, _ = _injected(m2, sig, latency_s=60)
    c = [f for f in f2 if f['act'] == 'close'][0]
    assert c['kind'] == 'stop' and int(t_k.value // 10 ** 6) <= c['t_ms'] < int(t_k.value // 10 ** 6) + 60_000


def test_latency_must_be_whole_minutes():
    with pytest.raises(X.ScalpExecError, match='whole number'):
        X.make_path_fn({}, {}, [], latency_s=30)


def _cal():
    import json
    with open(os.path.join(ROOT, 'research_evidence', 'prereg', 'trend_ema_mom.v1.json'), encoding='utf-8') as fh:
        return json.load(fh)['costs']['slip_cal_v1']


def test_account_path_applies_costs_at_fill_time_and_funding_at_T():
    m = _flat_minutes(300)
    sig = 280
    raw = {SYM: _agg(m)}
    t_e = int(raw[SYM].t[sig + 1].value // 10 ** 6)
    model = C.CostModel(_cal(), {SYM: 'crypto'})
    fund = {SYM: [(t_e, 0.001, 100.0), (t_e + 3 * 900_000, 0.001, 100.0)]}
    _set(m, raw[SYM].t[sig + 5], 100.0, 100.2, 98.5, 99.0)                # stop-out on candle sig + 5
    for lat, want_events in ((0, 2), (60, 1)):                             # at 60 s the entry is after T = t_e
        pr = X.Pricer(C.STRESS['base'], model, {SYM: _agg(m)}, funding=fund)
        _, out, fills, _ = _injected(m, sig, latency_s=lat, pricer=pr)
        ts = X.account_trades(fills, out['history'], pr.paid)
        assert len(ts) == 1 and len(pr.paid) == want_events
        e = [f for f in fills if f['act'] == 'open'][0]
        assert e['px'] > e['ref'] and e['fee'] > 0                         # adverse slippage + taker fee on the entry
        assert ts[0]['funding'] == pytest.approx(want_events * 0.001 * e['qty'] * 100.0)
        assert ts[0]['net'] < sum(f['qty'] * (f['ref'] - e['ref']) for f in fills if f['act'] == 'close')


def test_run_cell_reports_repriced_account_and_divergence():
    m = _minutes(420, 11)
    raw = {SYM: _agg(m)}
    model = C.CostModel(_cal(), {SYM: 'crypto'})
    fund = {SYM: [(int(t.value // 10 ** 6), 0.0001, 100.0) for t in raw[SYM].t[::32]]}
    pk = X.run_cell(raw, {SYM: m}, _sleeve(), T0, model=model, funding=fund, start=2000.0)
    assert pk['non_promotable'] is True and pk['status'] == 'COMPLETE' and pk['latency_s'] == 0
    assert set(pk['account']) == set(X.ACCOUNT_ROWS) and set(pk['divergence']) == set(X.ACCOUNT_ROWS)
    assert pk['repriced']['trades'] and set(pk['repriced']['summary']) == set(X.SCENARIOS)
    for name in X.ACCOUNT_ROWS:
        d = pk['divergence'][name]
        assert d['zero'] == len(pk['repriced']['trades']) and d['account'] == len(pk['account'][name]['trades'])
        assert set(pk['account'][name]['risk_fill']) == set(X.RF.ACTIONS)
    b, s = pk['account']['base']['summary']['ALL'], pk['account']['edge01_x1_5']['summary']['ALL']
    assert s['net_r'] < b['net_r'] + 1e-9 or s['n'] != b['n']              # stress costs more on the same decisions
    assert 'SLIP-C-UNFITTED' in pk['labels'] and pk['code']['engine.py']
    tr = X.trial_records(pk, key='exp_scalp', side='both')
    assert len(tr) == len(X.SCENARIOS) + len(X.ACCOUNT_ROWS)


def test_frozen_config_is_checked_by_sha_and_only_sides_change(tmp_path):
    import json
    doc = {'schema': 'zb-edge01-frozen-config/1', 'preset': 'breadth_rotation',
           'sleeves': [dict(id='exp_scalp', key='exp_scalp', name='x', enabled=True, share=1 / 6, risk=0.02, max_pos=4,
                            symbols='all', sides='both', mgmt=dict(be_r=0.75, tp1_frac=0.5, tp1_r=0.75), tf='15m')]}
    p = tmp_path / 'cfg.json'
    p.write_bytes(json.dumps(doc).encode())
    with pytest.raises(X.ScalpExecError, match='sha256'):
        X.load_config(str(p))
    import hashlib
    got = X.load_config(str(p), hashlib.sha256(p.read_bytes()).hexdigest())
    sl = X.cell_sleeves(got, 'exp_scalp', 'short')
    assert sl[0]['sides'] == 'short' and {k: v for k, v in sl[0].items() if k != 'sides'} == \
        {k: v for k, v in doc['sleeves'][0].items() if k != 'sides'}
    with pytest.raises(X.ScalpExecError, match='sleeves of range_scalp'):
        X.cell_sleeves(got, 'range_scalp', 'long')


def test_frozen_config_constant_matches_codex_file():
    r = subprocess.run(['git', 'show', 'origin/codex/strategy-evidence-policy:docs/newcore/research/'
                        'EDGE01_FROZEN_BASELINE_CONFIG_2026-10-23.json'], cwd=ROOT, capture_output=True)
    if r.returncode:
        pytest.skip('Codex policy branch not fetched')
    import hashlib
    assert hashlib.sha256(r.stdout).hexdigest() == X.CONFIG_SHA256


def test_skipping_durable_state_writes_changes_no_fill():
    m = _minutes(420, 11)
    raw = {SYM: _agg(m)}
    a = X._engine(raw, {SYM: m}, _sleeve(), T0, start=2000.0, rules=None, settings=None, latency_s=0)
    b = X._engine(raw, {SYM: m}, _sleeve(), T0, start=2000.0, rules=None, settings=None, latency_s=0, durable_io=True)
    assert a[1] == b[1] and len(a[1]) > 6


def test_f1_walk_is_time_merged_across_symbols():
    """Cowork #187 F1: with a different number of points per symbol, manage() must never see one symbol minutes ahead."""
    from replay import run_replay
    import engine as E
    m = _minutes(320, 3)
    raw = {SYM: _agg(m), 'ETHUSDT': _agg(m)}
    seen = []
    real = E.Engine.manage

    def spy(self, marks):
        seen.append(dict(marks)); return real(self, marks)

    def fn(j, held, lots):
        t = int(raw[SYM].t[j].value // 10 ** 6)
        a = [(100.0 + k * 0.001, False, t + k * 60_000) for k in range(15)]
        b = [(200.0 + k * 0.001, False, t + k * 60_000) for k in range(15) for _ in range(3)]   # 3 points per minute
        return {SYM: a, 'ETHUSDT': b}
    sig_t = raw[SYM].t[290]
    real_sig = S.signals

    def fake(key, d, ctx, params=None, mask_sides=True):                  # one long on SYM so lots are open while walked
        out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
        out['le'][:] = (pd.to_datetime(d.t) == sig_t).values
        return out
    E.Engine.manage, S.signals = spy, fake
    try:
        run_replay(copy.deepcopy(raw), _sleeve(mgmt=dict(stop_atr=50.0, tp_r=50.0, max_bars=500)), T0, tf_sec=900,
                   compare=False, path_fn=fn, costs_off=True)
    finally:
        E.Engine.manage, S.signals = real, real_sig
    walked = [x for x in seen if 100.0 <= x.get(SYM, 0) < 101 and 200.0 <= x.get('ETHUSDT', 0) < 201]
    assert walked
    for x in walked:
        k_a = round((x[SYM] - 100.0) / 0.001); k_b = round((x['ETHUSDT'] - 200.0) / 0.001)
        assert abs(k_a - k_b) <= 1                                         # same minute (or the next symbol's turn)


# ------------------------------------------------------------------ B1 (Cowork ruling 1): measured fills, harness only
def _b1_run(measured, decide=None):
    import engine as E
    from replay import run_replay
    m = _flat_minutes(300)
    raw = {SYM: _agg(m)}
    sig_t = raw[SYM].t[280]
    real, real_dec = S.signals, E.RF.decide

    def fake(key, d, ctx, params=None, mask_sides=True):
        out = {k: np.zeros(len(d), bool) for k in ('le', 'se', 'lx', 'sx')}
        out['le'][:] = (pd.to_datetime(d.t) == sig_t).values
        return out

    def fee_px(f): return f['ref'], (f['qty'] * f['ref'] * 0.005 if f['act'] == 'open' else 0.0)
    S.signals, fills = fake, []
    if decide: E.RF.decide = decide(real_dec)
    try:
        sl = [E.sleeve('SC', 'exp_scalp', 1.0, 0.01, 1, [SYM], sides='long', mgmt={}, tf='15m')]
        out = run_replay(copy.deepcopy(raw), sl, T0, tf_sec=900, compare=False, fill_log=fills, fill_px=fee_px,
                         measured_fills=measured, path_fn=X.make_path_fn(raw, {SYM: m}, [])[0])
    finally:
        S.signals, E.RF.decide = real, real_dec
    return out, fills


def test_b1_measured_fills_reach_risk_fill_and_b0_is_unchanged():
    b0, f0 = _b1_run(False)
    b1, f1 = _b1_run(True)
    assert f0 == f1 and len(f0) == 3                                   # same fills: the booked price IS the measured one
    assert b0['history'][0]['risk_fill'] == 'unavailable'                  # B0: RISK-FILL-01 never sees a measured fill
    rf = b1['history'][0]['risk_fill']['0']
    assert rf['action'] == 'ok' and rf['fee_basis'] == 'actual' and rf['measured_px'] == 100.0


def test_b1_a_trim_decision_is_executed_through_the_fake():
    def worse(real):                                                     # the measured price 0.5 above the booked one (long)
        return lambda side, qty, meas, *a, **k: real(side, qty, meas + 0.5, *a, **k)
    out, fills = _b1_run(True, worse)
    rf = out['history'][0]['risk_fill']['0']
    assert rf['action'] == 'trim' and rf['state'] == 'done' and rf['trimmed'] > 0
    closes = [f for f in fills if f['act'] == 'close']
    assert 0 < closes[0]['t_ms'] - fills[0]['t_ms'] < 60_000 and closes[0]['qty'] == pytest.approx(rf['trimmed'])    # first managed point
    assert sum(f['qty'] for f in closes) == pytest.approx(fills[0]['qty'])
