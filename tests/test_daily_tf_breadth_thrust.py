"""DAILY-TF-01: 1d candles in the runtime + the CP-Y2 breadth thrust PAPER canary (default off).

1. module: import registers nothing, frozen params / management equal the research case, PAPER-testnet-only registration;
2. parity: on real repo candles (data/*_4h.csv -> complete UTC days, BTC + the 31 screened coins) the module equals a
   verbatim copy of the frozen research function (PR #337 646ab3fa research/newstrat_cp3.py sig_breadth_thrust), coin by
   coin, both sides, and fires on both sides;
3. engine: the UNCHANGED Engine.compute_signals on a fake engine at '1d' - the breadth panel is the frozen BTC + 31 even
   when other coins are in the cycle, its last-candle signal at each cut equals the full-history signal (prefix
   causality), and a missing / stale / too-short panel coin gives no signal at all (fails closed);
4. wiring: TF_SEC / validate_sleeve accept 1d, research-only stays refused until registered, a slot must list panel coins
   at 1d, the daily candle cache drops the unclosed day, the app backtest refuses it, re-entry lock, and the strategy maps
   to its own TRUE-BOOK (B5_DISCOVERY) instead of being refused.
No network, settings, credentials or app instance.
"""
import importlib
import os
import time
from functools import lru_cache
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import engine as E
import paper_books as PB
import strategies as S
import true_books as TB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BTC = 'BTCUSDT'
EXTRA31 = ['1000PEPEUSDT', 'AAVEUSDT', 'APTUSDT', 'ARBUSDT', 'ARKUSDT', 'BCHUSDT', 'DOTUSDT', 'ENAUSDT', 'ENJUSDT',
           'ETCUSDT', 'FETUSDT', 'FILUSDT', 'GALAUSDT', 'INJUSDT', 'LTCUSDT', 'MANAUSDT', 'MOVRUSDT', 'NEARUSDT',
           'ONDOUSDT', 'ONEUSDT', 'QNTUSDT', 'SANDUSDT', 'STRKUSDT', 'SUIUSDT', 'SUPERUSDT', 'TAOUSDT', 'UNIUSDT',
           'WLDUSDT', 'XLMUSDT', 'ZECUSDT', 'ZROUSDT']
OTHERS = ['ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'DOGEUSDT']          # in the cycle, NOT in the slot
BASE = 'https://demo-fapi.binance.com'


@pytest.fixture(autouse=True)
def restore_registry():
    old = S.STRATEGIES.copy()
    yield
    S.STRATEGIES.clear()
    S.STRATEGIES.update(old)


def module():
    import breadth_thrust_daily as m
    return importlib.reload(m)


# ------------------------------------------------------------------ frozen research reference (verbatim, 646ab3fa)
REF_PANEL = {}


def ref_set_panel(raw):
    REF_PANEL['c'] = pd.DataFrame({s: pd.Series(d.c.values, index=pd.to_datetime(d.t)) for s, d in raw.items()}).sort_index()


def ref_sig_breadth_thrust(d, p, ctx):
    c = REF_PANEL['c']
    above = (c > c.ewm(span=20, adjust=False, min_periods=20).mean()).where(c.notna())
    br = above.sum(axis=1) / c.notna().sum(axis=1).replace(0, np.nan)
    up = (br >= p['hi']) & (br.rolling(p['win']).min().shift() <= p['lo'])
    dn = (br <= p['lo']) & (br.rolling(p['win']).max().shift() >= p['hi'])
    up = up & ~up.shift().fillna(False).astype(bool); dn = dn & ~dn.shift().fillna(False).astype(bool)
    t = pd.to_datetime(d.t)
    u = pd.Series(up.reindex(t).fillna(False).values, index=d.index)
    w = pd.Series(dn.reindex(t).fillna(False).values, index=d.index)
    le = u & (d.c > d.e21)
    se = w & (d.c < d.e21)
    z = pd.Series(False, index=d.index)
    return dict(le=S.X(le), se=S.X(se), lx=z, sx=z)


REF_CASE = dict(tf='1d', side='both', params=dict(lo=0.25, hi=0.75, win=5),
                management=dict(stop_atr=2.5, tp_r=3.0, max_bars=20, tp1_r=1.0, tp1_frac=0.5, be_r=1.0))


# ------------------------------------------------------------------ real candles -> complete UTC days
@lru_cache(maxsize=None)
def daily_raw():
    out = {}
    for s in [BTC] + EXTRA31 + OTHERS:
        d = pd.read_csv(os.path.join(ROOT, 'data', f'{s}_4h.csv'), encoding='utf-8', parse_dates=['t'])
        g = d.set_index('t').resample('1D', label='left', closed='left')
        x = g.agg(dict(o='first', h='max', l='min', c='last', v='sum'))
        x = x[(g.size() == 6).values].dropna(subset=['o', 'h', 'l', 'c']).reset_index()
        out[s] = x
    return out


@lru_cache(maxsize=None)
def panel_frames():
    return {s: S.indicators(daily_raw()[s]) for s in [BTC] + EXTRA31}


# ------------------------------------------------------------------ 1. module

# ------------------------------------------------------------------ 1. module
def test_import_is_default_off_and_frozen():
    before = S.STRATEGIES.copy()
    m = module()
    assert S.STRATEGIES == before and m.KEY not in S.STRATEGIES
    r = m.RESEARCH
    assert r['research_only'] is True and r['fn'] is m.signal and r['reentry_lock'] is True
    assert r['panel_symbols'] == (BTC,) + tuple(EXTRA31)
    assert (r['tf'], r['sides'], r['params'], r['mgmt']) == (REF_CASE['tf'], REF_CASE['side'], REF_CASE['params'],
                                                             REF_CASE['management'])


@pytest.mark.parametrize('mode,base', [
    ('live', BASE), ('PAPER', BASE), ('paper', 'https://fapi.binance.com'), ('paper', 'http://demo-fapi.binance.com'),
    ('paper', 'https://demo-fapi.binance.com:443'), ('paper', 'https://demo-fapi.binance.com.evil.example'),
    ('paper', 'https://demo-fapi.binance.com/'),
])
def test_refusal_never_mutates_registry(mode, base):
    m = module(); before = S.STRATEGIES.copy()
    with pytest.raises(ValueError):
        m.register_for_paper(mode, base)
    assert S.STRATEGIES == before


@pytest.mark.parametrize('base', ['https://testnet.binancefuture.com', BASE])
def test_registration_collision_and_management(base):
    m = module()
    S.STRATEGIES[m.KEY] = foreign = {'fn': object()}
    with pytest.raises(ValueError):
        m.register_for_paper('paper', base)
    assert S.STRATEGIES[m.KEY] is foreign
    del S.STRATEGIES[m.KEY]
    entry = m.register_for_paper('paper', base)
    assert m.register_for_paper('paper', base) is entry
    assert entry['research_only'] is False and m.RESEARCH['research_only'] is True
    assert entry['mgmt'] == REF_CASE['management'] and entry['params'] == REF_CASE['params'] and entry['tf'] == '1d'
    assert entry['mgmt'] is not m.RESEARCH['mgmt'] and entry['params'] is not m.RESEARCH['params']
    assert not ({'runner', 'prot', 'ttp', 'pyramid', 'dca', 'trail_atr'} & entry['mgmt'].keys())


def test_no_or_partial_panel_fails_closed():
    m = module(); frames = panel_frames(); d = frames['AAVEUSDT']
    pc = S.build_context(frames, BTC)['AAVEUSDT']['panel_c']
    for ctx in ({}, None, {'panel_c': None}, {'panel_c': pd.DataFrame()}, {'panel_c': pc.drop(columns=['ZROUSDT'])},
                {'panel_c': pc.drop(columns=[BTC])}):
        r = m.signal(d, m.PARAMS, ctx)
        assert not any(np.asarray(r[k]).any() for k in ('le', 'se', 'lx', 'sx'))


# ------------------------------------------------------------------ 2. parity with the frozen research function
def test_parity_with_frozen_research_function_on_real_candles():
    m = module()
    frames = panel_frames()
    ref_set_panel({s: daily_raw()[s] for s in frames})
    ctx = S.build_context(frames, BTC)
    n_le = n_se = 0
    for s in EXTRA31:
        d = frames[s]
        a = ref_sig_breadth_thrust(d, REF_CASE['params'], None)
        b = m.signal(d, m.PARAMS, ctx[s])
        for k in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(np.asarray(a[k], bool), np.asarray(b[k], bool)), (s, k)
        n_le += int(np.asarray(b['le']).sum()); n_se += int(np.asarray(b['se']).sum())
    assert n_le > 0 and n_se > 0, (n_le, n_se)             # both sides really fire on this history


# ------------------------------------------------------------------ 3. engine compute_signals at 1d (unchanged code)
def _fake(cut, cycle_syms, slot_syms, drop=(), stale=(), short=()):
    m = module(); S.STRATEGIES.pop(m.KEY, None); m.register_for_paper('paper', BASE)
    sl = E.sleeve('BRT', m.KEY, 1.0, .01, 3, symbols=list(slot_syms), sides='both', tf='1d')
    raw = daily_raw()
    frames = {}
    for s in sorted(set(cycle_syms) | {BTC}):
        if s in drop: continue
        d = raw[s][raw[s].t <= cut]
        if s in stale: d = d.iloc[:-1]
        if s in short: d = d.iloc[-200:]
        frames[s] = S.indicators(d.reset_index(drop=True))
    fake = SimpleNamespace(S={'SLEEVES': [sl]}, rules={s: {} for s in raw}, _sig_raw={}, candles=lambda s, tf: frames[s])
    return fake, m


@lru_cache(maxsize=None)
def full_signals(panel=None):
    """{(sym, day): (le, se)} on the whole history; panel_c built over BTC + `panel` coins (default: the frozen 31)."""
    m = module(); raw = daily_raw()
    frames = {s: S.indicators(raw[s]) for s in (BTC,) + tuple(panel or EXTRA31)}
    ctx = S.build_context(frames, BTC); out = {}
    for s in EXTRA31:
        r = m.signal(frames[s], m.PARAMS, ctx[s])
        for i in np.flatnonzero(np.asarray(r['le']) | np.asarray(r['se'])):
            out[(s, frames[s].t.iloc[i])] = (bool(r['le'][i]), bool(r['se'][i]))
    return out


def _cuts():
    days = sorted({t for (_, t) in full_signals()})
    sig = [t for t in days if t >= daily_raw()[BTC].t.iloc[300]][:4]       # >= 250 closed days at the cut
    quiet = [t - pd.Timedelta(days=2) for t in sig[:2]]
    return sig + quiet + [_wide_diff_day()]


def _wide_diff_day():
    """A day on which breadth over BTC + 31 + 4 others would give a different signal than the frozen panel."""
    m = module(); raw = daily_raw()
    wide = {s: S.indicators(raw[s]) for s in [BTC] + EXTRA31 + OTHERS}
    pc = S.build_context(wide, BTC)[BTC]['panel_c']
    up_f, dn_f = m.thrust(m.breadth(pc[list(m.PANEL_SYMBOLS)]), m.PARAMS)
    up_w, dn_w = m.thrust(m.breadth(pc), m.PARAMS)
    lim = raw[BTC].t.iloc[300]
    days = [t for t in pc.index if t >= lim and (up_f[t] != up_w[t] or dn_f[t] != dn_w[t])]
    assert days, 'no discriminating day: the panel-scope check would be vacuous'
    return days[0]


def test_engine_daily_signals_equal_full_history_with_other_coins_in_the_cycle():
    want = full_signals()
    cuts = _cuts()
    assert len(cuts) >= 5
    hits = 0
    for cut in cuts:
        fake, m = _fake(cut, EXTRA31 + OTHERS, EXTRA31)
        out, al = E.Engine.compute_signals(fake, '1d', sorted(EXTRA31 + OTHERS))
        for s in EXTRA31:
            sg = out[f'BRT|{s}']
            assert (sg['le'], sg['se']) == want.get((s, cut), (False, False)), (s, cut)
            assert sg['time'] == str(cut)
            hits += sg['le'] or sg['se']
    assert hits > 0


@pytest.mark.parametrize('how', ['missing', 'stale', 'btc_missing', 'short_history'])
def test_incomplete_panel_gives_no_signal(how):
    cut = _cuts()[0]
    kw = dict(missing=dict(drop=('ZROUSDT',)), stale=dict(stale=('AAVEUSDT',)), btc_missing=dict(drop=(BTC,)),
              short_history=dict(short=('ZROUSDT',)))[how]          # < 250 closed days: the engine drops the coin
    fake, m = _fake(cut, EXTRA31, EXTRA31, **kw)
    out, al = E.Engine.compute_signals(fake, '1d', sorted(EXTRA31))
    assert not any(out[k]['le'] or out[k]['se'] for k in out if k.startswith('BRT|'))
    fake, m = _fake(cut, EXTRA31, EXTRA31)                    # the same candle with a complete panel does signal
    out, al = E.Engine.compute_signals(fake, '1d', sorted(EXTRA31))
    assert any(out[f'BRT|{s}']['le'] or out[f'BRT|{s}']['se'] for s in EXTRA31)


def test_build_context_only_gains_panel_c():
    frames = panel_frames()
    ctx = S.build_context(frames, BTC)
    assert set(ctx[BTC]) == {'btc_down', 'rank', 'rank7', 'rank7_n', 'breadth7', 'n_symbols', 'panel_c'}
    pc = ctx[BTC]['panel_c']
    assert ctx['AAVEUSDT']['panel_c'] is pc and list(pc.columns) == list(frames) and pc.index.is_monotonic_increasing
    for s, d in frames.items():
        assert np.array_equal(pc[s].reindex(pd.to_datetime(d.t)).values, d.c.values)


# ------------------------------------------------------------------ 4. wiring
def test_tf_sec_and_validate_sleeve():
    import app as A
    assert E.TF_SEC['1d'] == 86400 and A.TF_SEC is E.TF_SEC
    m = module()
    raw = dict(key=m.KEY, share=0.2, risk=0.02, max_pos=3, symbols=list(EXTRA31), sides='both')
    with pytest.raises(ValueError):                               # not registered: unknown
        A.validate_sleeve(dict(raw), 0)
    S.STRATEGIES[m.KEY] = dict(m.RESEARCH)
    with pytest.raises(ValueError, match='research-only'):        # registered research entry: still refused
        A.validate_sleeve(dict(raw), 0)
    del S.STRATEGIES[m.KEY]
    m.register_for_paper('paper', BASE)
    out = A.validate_sleeve(dict(raw), 0)
    assert out['tf'] == '1d' and out['key'] == m.KEY and out['symbols'] == list(EXTRA31)
    assert A.validate_sleeve(dict(raw, symbols=['AAVEUSDT', 'ZROUSDT']), 0)['symbols'] == ['AAVEUSDT', 'ZROUSDT']
    for bad in (dict(symbols='all'), dict(symbols='core8'), dict(symbols=[]), dict(symbols=EXTRA31 + ['ETHUSDT']),
                dict(tf='4h'), dict(tf='2d')):
        with pytest.raises(ValueError):
            A.validate_sleeve(dict(raw, **bad), 0)
    ema = A.validate_sleeve(dict(key='ema_st', share=0.2, risk=0.02, max_pos=1, symbols='all', tf='1d'), 0)
    assert ema['tf'] == '1d'                                      # 1d is a general runtime timeframe now


def test_daily_candle_cache_drops_the_unclosed_day():
    day = 86400 * 1000
    now = time.time()
    today = (int(now) // 86400) * day
    rows = [[today - k * day, 1, 2, 0.5, 1.5, 10, today - k * day + day - 1, 15] for k in range(300, -1, -1)]   # incl. today
    calls = []
    fake = SimpleNamespace(_kc={}, data=SimpleNamespace(klines=lambda s, tf, n: calls.append((s, tf, n)) or rows))
    df = E.Engine.candles(fake, 'BTCUSDT', '1d')
    assert calls == [('BTCUSDT', '1d', 1500)]
    assert df.t.iloc[-1] == pd.Timestamp(today - day, unit='ms') and len(df) == 300
    E.Engine.candles(fake, 'BTCUSDT', '1d')                       # cached until the next daily close
    assert len(calls) == 1


def test_book_mapping_is_discovery_not_refused():
    m = module()
    assert TB.book_of(m.KEY) == 'B5_DISCOVERY' == PB.book_for(m.KEY)
    assert sum(m.KEY in keys for _k, _n, keys in PB.BOOKS) == 1


def test_reentry_lock_blocks_the_same_decision_candle_after_a_stop():
    """A restart or a manual run re-reads the same closed day: a coin stopped out after entering on it is not re-entered."""
    m = module(); S.STRATEGIES.pop(m.KEY, None); m.register_for_paper('paper', BASE)
    assert S.STRATEGIES[m.KEY]['reentry_lock'] is True
    sl = E.sleeve('BRT', m.KEY, 1.0, .01, 3, symbols=['AAVEUSDT'], sides='both', tf='1d')
    hist = [dict(sleeve='BRT', symbol='AAVEUSDT', side='LONG', closed='2026-08-20T13:00:00+00:00', exit_reason='stop')]
    e = SimpleNamespace(history=hist)
    assert E.Engine.reentry_lock(e, sl, 'AAVEUSDT', 'LONG', dict(time='2026-08-19 00:00:00'))
    assert E.Engine.reentry_lock(e, sl, 'AAVEUSDT', 'LONG', dict(time='2026-08-21 00:00:00')) is None
    assert E.Engine.reentry_lock(e, sl, 'AAVEUSDT', 'SHORT', dict(time='2026-08-19 00:00:00')) is None


def test_app_backtest_refuses_panel_strategies(monkeypatch):
    import threading
    import app as A
    m = module(); S.STRATEGIES.pop(m.KEY, None); m.register_for_paper('paper', BASE)
    e = SimpleNamespace(S={'SLEEVES': [], 'UNIVERSE': EXTRA31}, lock=threading.RLock(), state={'lots': []})
    monkeypatch.setattr(A, 'APP', SimpleNamespace(engine=e, lock=threading.RLock(), cfg={'MODE': 'paper'}))
    seen = []
    monkeypatch.setattr(A, 'enqueue', lambda fn, jid, req: seen.append(req))
    sl = dict(key=m.KEY, share=0.2, risk=0.02, max_pos=3, symbols=list(EXTRA31))
    with pytest.raises(ValueError, match='panel'):
        A.handle('/api/backtest', {'sleeves': [sl], 'universe': EXTRA31})
    assert not seen
    A.handle('/api/backtest', {'sleeves': [dict(sl, key='ema_st')], 'universe': EXTRA31})
    assert len(seen) == 1


def test_coin_frame_behind_the_panel_fails_closed():
    """The traded coin's own frame must end on the panel's latest candle (defence beyond the panel completeness check)."""
    m = module(); frames = panel_frames(); ctx = S.build_context(frames, BTC)
    d = frames['AAVEUSDT']
    assert np.asarray(m.signal(d, m.PARAMS, ctx['AAVEUSDT'])['le']).any()
    r = m.signal(d.iloc[:-1], m.PARAMS, ctx['AAVEUSDT'])
    assert not any(np.asarray(r[k]).any() for k in ('le', 'se'))

