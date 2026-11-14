"""R6-PAPER-01: default-OFF r6_regime_flip_4h PAPER strategy + account-4 overlay (no deployment).

Covers: import registers nothing; PAPER + exact testnet origin guard; registration cleanup; the frozen rule equals the
research rule (verbatim reference copy of longscout_v1/v6 @ acc8d0ee) and Codex's independent formulation; prefix
causality; D01 admission (720-hour run, prior-30-complete-day median quote volume, exact cutoff) against Codex's fit
runner formulation; fail-closed entries (stale / gapped / unknown symbol / fetch failure / budget); exits never gated;
management (3 ATR, 120 bars, nothing else); overlay guards (PAPER, origin, book mode, missing rules, exact-list panel
exception, slot add/enable/remove); weight pacer; and an engine receipt: EX14 sizing $8 risk / $3,333.33 notional cap
through the real engine open_lot on account-4 settings, with the explicit 163 list bypassing UNIVERSE.
"""
import io
import importlib
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategies as S                                    # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')


@pytest.fixture(autouse=True)
def restore_registry():
    old = S.STRATEGIES.copy()
    yield
    import r6_regime_flip as R
    R.unregister()
    S.STRATEGIES.clear()
    S.STRATEGIES.update(old)


def mod():
    import r6_regime_flip as R
    R.unregister()
    return importlib.reload(R)


# ---------------------------------------------------------------- research reference (verbatim, acc8d0ee)
def _ref_bar(t):
    t = pd.to_datetime(pd.Series(t)).reset_index(drop=True)
    step = t.diff().median() if len(t) > 1 else pd.Timedelta(hours=4)
    return t, (step if pd.notna(step) and step > pd.Timedelta(0) else pd.Timedelta(hours=4))


def _ref_daily_closes(t, c):
    t, step = _ref_bar(t)
    s = pd.Series(np.asarray(c, float), index=pd.DatetimeIndex(t))
    if step >= pd.Timedelta(days=1):
        return s
    g = s.resample('1D')
    last, cnt = g.last(), g.count()
    return last[cnt == int(pd.Timedelta(days=1) / step)].dropna()


def _ref_regime_daily(dc):
    sma = dc.rolling(100, min_periods=100).mean()
    slope = sma - sma.shift(20)
    r = pd.Series(0.0, index=dc.index)
    r[(dc > sma) & (slope > 0)] = 1.0
    r[(dc < sma) & (slope < 0)] = -1.0
    r[np.arange(len(dc)) < 119] = np.nan
    r[sma.isna() | slope.isna()] = np.nan
    return r


def _ref_on_rows(daily_vals, t):
    t, step = _ref_bar(t)
    left = pd.DataFrame({'ct': (t + step).astype('datetime64[ns]'), 'row': np.arange(len(t))})
    right = pd.DataFrame({'avail': (pd.DatetimeIndex(daily_vals.index) + pd.Timedelta(days=1)).astype('datetime64[ns]'),
                          'v': daily_vals.values}).sort_values('avail')
    out = pd.merge_asof(left, right, left_on='ct', right_on='avail', direction='backward').sort_values('row')
    return out['v'].values


def _ref_first(x):
    x = S.X(x)
    return x & ~x.shift(fill_value=False)


def ref_sig_regime_flip(d, p=None, ctx=None):
    cr = pd.Series(_ref_on_rows(_ref_regime_daily(_ref_daily_closes(d.t, d.c)), d.t), index=d.index)
    up, dn = cr == 1, cr == -1
    return dict(le=_ref_first(up), se=_ref_first(dn), lx=S.X(~up), sx=S.X(~dn))


def codex_regime(d):
    """Codex independent fit runner formulation (7a81c7fd), regime part only."""
    dayg = d.set_index('t').c.resample('1D'); dc = dayg.last()[dayg.count() == 6]; sma = dc.rolling(100).mean(); slope = sma - sma.shift(20)
    reg = np.where((dc > sma) & (slope > 0), 1, np.where((dc < sma) & (slope < 0), -1, 0)).astype(float); reg[slope.isna()] = np.nan
    available = pd.Series(reg, index=dc.index + pd.Timedelta(days=1))
    return available.reindex(pd.DatetimeIndex(d.t) + pd.Timedelta(hours=4), method='ffill').values


def codex_liq(h):
    """Codex fit runner D01 lines (7a81c7fd), verbatim; h = DataFrame t, qv (1h). -> liq Series by 1h open time."""
    day=h.t.dt.floor('D'); q=h.groupby(day).qv.sum().where(h.groupby(day).size()==24); q=q.reindex(pd.date_range(q.index.min(),q.index.max(),freq='D')); med=q.rolling(30).median().shift()
    runs=h.groupby(h.t.diff().ne(pd.Timedelta(hours=1)).cumsum()).cumcount()+1
    liq=pd.Series((med.reindex(day).values>=1e7)&(runs.values>=720),index=h.t)
    return liq


def load4h(sym):
    d = pd.read_csv(os.path.join(DATA, f'{sym}_4h.csv'), encoding='utf-8')
    d['t'] = pd.to_datetime(d.t)
    return d


# ---------------------------------------------------------------- registration
def test_import_registers_nothing_and_research_entry_is_research_only():
    before = S.STRATEGIES.copy()
    R = mod()
    assert S.STRATEGIES == before and R.KEY not in S.STRATEGIES
    assert R.RESEARCH['research_only'] is True
    import r6_paper_overlay  # noqa: F401  (overlay import registers nothing either)
    assert S.STRATEGIES == before


@pytest.mark.parametrize('mode,base', [
    ('live', 'https://demo-fapi.binance.com'), ('PAPER', 'https://demo-fapi.binance.com'),
    ('paper', 'https://fapi.binance.com'), ('paper', 'http://demo-fapi.binance.com'),
    ('paper', 'https://demo-fapi.binance.com:443'), ('paper', 'https://demo-fapi.binance.com/'),
    ('paper', None)])
def test_registration_requires_paper_and_exact_testnet_origin(mode, base):
    R = mod()
    with pytest.raises(ValueError):
        R.register_for_paper(mode, base, lambda d: None, lambda *a: None)
    assert R.KEY not in S.STRATEGIES and R._CTX['resolve'] is None


def test_registration_needs_data_context_and_refuses_foreign_key():
    R = mod()
    with pytest.raises(ValueError):
        R.register_for_paper('paper', 'https://demo-fapi.binance.com', None, lambda *a: None)
    S.STRATEGIES[R.KEY] = dict(S.STRATEGIES['ema_mom'])
    with pytest.raises(ValueError):
        R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda d: None, lambda *a: None)


def test_registration_is_idempotent_and_unregister_cleans_up():
    R = mod()
    a = R.register_for_paper('paper', 'https://testnet.binancefuture.com', lambda d: None, lambda *a: None)
    b = R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda d: None, lambda *a: None)
    assert a is b and S.STRATEGIES[R.KEY] is a and a['research_only'] is False and a['tf'] == '4h' and a['sides'] == 'both'
    R.LAST_GATE['X'] = {}; R._D01_CACHE[('X', 't')] = (True, '')
    R.unregister()
    assert R.KEY not in S.STRATEGIES and R._CTX['resolve'] is None and R._CTX['fetch_1h'] is None
    assert not R.LAST_GATE and not R._D01_CACHE


def test_frozen_symbol_list_is_exact():
    R = mod()
    fz = dict(symbols=list(R.SYMBOLS))
    assert len(R.SYMBOLS) == 163 and list(R.SYMBOLS) == sorted(set(R.SYMBOLS))
    assert 'ICPUSDT' not in R.SYMBOLS and 'TLMUSDT' not in R.SYMBOLS and 'BTCUSDT' not in R.SYMBOLS
    assert R.symbols_sha256() == R.SYMBOLS_SHA256 == '7a28fc2d5a4150737075484849097dda865107b6497e65583cec952e52f3fa0f'
    assert fz['symbols'][0] == '1000BONKUSDT' and fz['symbols'][-1] == 'ZRXUSDT'


def test_management_is_exactly_3atr_and_120_bars():
    R = mod()
    R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda d: None, lambda *a: None)
    assert S.merge_mgmt(R.KEY, {}) == {'stop_atr': 3.0, 'max_bars': R.BACKSTOP_BARS} and R.MAX_BARS == 120


# ---------------------------------------------------------------- frozen rule parity + causality
COINS = sorted(f[:-7] for f in os.listdir(DATA) if f.endswith('_4h.csv'))


def test_raw_rule_equals_research_and_codex_on_all_repo_coins():
    R = mod()
    n_flip = 0
    for sym in COINS:
        d = load4h(sym)
        a, b = R.raw_signals(d), ref_sig_regime_flip(d)
        for k in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(a[k].values, b[k].values), (sym, k)
        cr = codex_regime(d)
        up, dn = pd.Series(cr == 1), pd.Series(cr == -1)
        assert np.array_equal(a['le'].values, (up & ~up.shift(fill_value=False)).values), sym
        assert np.array_equal(a['se'].values, (dn & ~dn.shift(fill_value=False)).values), sym
        assert np.array_equal(a['lx'].values, ~up.values) and np.array_equal(a['sx'].values, ~dn.values), sym
        n_flip += int(a['le'].sum() + a['se'].sum())
    assert len(COINS) >= 30 and n_flip > 100


def test_raw_rule_is_prefix_causal():
    R = mod()
    d = load4h('AAVEUSDT')
    full = R.raw_signals(d)
    for cut in (800, 1301, 2222, 3333, 4100, len(d) - 1):
        p = R.raw_signals(d.iloc[:cut].reset_index(drop=True))
        for k in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(p[k].values, full[k].values[:cut]), (cut, k)


def test_flip_only_on_the_row_that_sees_a_new_daily_close():
    R = mod()
    d = load4h('AAVEUSDT')
    a = R.raw_signals(d)
    rows = d.t[a['le'] | a['se']]
    assert len(rows) and (rows.dt.hour == 20).all()          # the 20:00 row closes at 00:00: the day's close is known


# ---------------------------------------------------------------- D01 admission
def h1(start, end, qv=1e6, drop=()):
    t = pd.date_range(start, end, freq='1h')
    h = pd.DataFrame(dict(t=t, qv=np.full(len(t), float(qv))))
    return h[~h.t.isin(pd.DatetimeIndex(drop))].reset_index(drop=True)


DEC = pd.Timestamp('2026-10-20 20:00')                      # decision bar open; cutoff candle 23:00


def test_d01_pass_and_threshold():
    R = mod()
    ok, why = R.d01_check(h1('2026-09-18', DEC + pd.Timedelta(hours=3)), DEC)
    assert ok, why
    ok, why = R.d01_check(h1('2026-09-18', DEC + pd.Timedelta(hours=3), qv=1e7 / 24 - 1), DEC)
    assert not ok and 'median' in why
    ok, why = R.d01_check(h1('2026-09-18', DEC + pd.Timedelta(hours=3), qv=1e7 / 24), DEC)
    assert ok, why                                           # >= 1e7 exactly passes (as the fit runner)


def test_d01_run_and_day_completeness_and_cutoff():
    R = mod()
    end = DEC + pd.Timedelta(hours=3)
    ok, why = R.d01_check(h1('2026-09-18', end, drop=[DEC - pd.Timedelta(hours=100)]), DEC)
    assert not ok and 'run' in why                           # a missing hour inside the last 720
    ok, why = R.d01_check(h1('2026-09-18', end, drop=[end]), DEC)
    assert not ok and 'cutoff' in why                        # the decision bar's last hour must exist
    ok, why = R.d01_check(h1('2026-09-25', end), DEC)
    assert not ok and ('run' in why or '30' in why)
    # future candles are ignored: huge volume after the cutoff cannot admit a thin coin
    thin = h1('2026-09-18', end, qv=1)
    fut = h1(end + pd.Timedelta(hours=1), end + pd.Timedelta(days=40), qv=1e12)
    assert not R.d01_check(pd.concat([thin, fut], ignore_index=True), DEC)[0]
    ok, why = R.d01_check(None, DEC); assert not ok
    bad = h1('2026-09-18', end); bad.loc[5, 'qv'] = np.nan
    assert not R.d01_check(bad, DEC)[0]


def test_d01_matches_codex_fit_runner_on_random_gapped_series():
    R = mod()
    rng = np.random.default_rng(14)
    for trial in range(60):
        t = pd.date_range('2026-07-01', '2026-10-31 23:00', freq='1h')
        keep = rng.random(len(t)) > (0.0 if trial % 3 == 0 else 0.0004)
        qv = rng.lognormal(np.log(4.5e5), 0.6, len(t))
        h = pd.DataFrame(dict(t=t[keep], qv=qv[keep])).reset_index(drop=True)
        liq = codex_liq(h)
        for _ in range(12):
            dec = pd.Timestamp('2026-08-15') + pd.Timedelta(hours=4 * int(rng.integers(0, 6 * 70)))
            cut = dec + pd.Timedelta(hours=3)
            want = bool(liq.reindex([cut]).fillna(False).iloc[0])
            # live sees only its fetch window (31 days before the decision day .. cutoff)
            win = h[(h.t >= dec.floor('D') - pd.Timedelta(days=31)) & (h.t <= cut)]
            assert R.d01_check(win, dec)[0] == want, (trial, dec)


# ---------------------------------------------------------------- fail-closed entries, exits never gated
def flip_frame(side='le'):
    R = mod()
    d = load4h('AAVEUSDT')
    a = R.raw_signals(d)
    idx = [i for i in np.flatnonzero(a[side].values) if i >= R.R1_CONTIG_BARS + 10]
    i = idx[-1]
    return d.iloc[:i + 1].reset_index(drop=True)


def ctx_for(R, d, sym='AAVEUSDT', fetch=None, clock=None):
    end = pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4)
    calls = []

    def f(s, a, b):
        calls.append((s, a, b))
        return h1(a, b - pd.Timedelta(hours=1))
    R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda x: sym if x is d else None, fetch or f,
                         clock=clock or (lambda: end.timestamp() + 90))
    return calls


def sig(R, d):
    return S.signals(R.KEY, d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False)


@pytest.mark.parametrize('side', ['le', 'se'])
def test_admitted_entry_only_on_the_decision_row(side):
    d = flip_frame(side)
    R = mod()
    calls = ctx_for(R, d)
    out = sig(R, d)
    assert out[side][-1] and out[side].sum() == 1               # history rows never carry an entry
    other = 'se' if side == 'le' else 'le'
    assert not out[other].any()
    assert len(calls) == 1 and calls[0][0] == 'AAVEUSDT'
    s, a, b = calls[0]
    assert b == pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4) and a == pd.Timestamp(d.t.iloc[-1]).floor('D') - pd.Timedelta(days=31)
    assert R.LAST_GATE['AAVEUSDT']['ok'] is True
    sig(R, d)                                                    # cached: no second fetch for the same bar
    assert len(calls) == 1


def test_unregistered_research_fn_never_enters_and_paper_without_context_refuses():
    d = flip_frame('le')
    R = mod()
    out = R.signal(d, {}, {})
    assert not out['le'].any() and not out['se'].any()
    assert R.LAST_GATE['?']['reason'].startswith('not registered')


@pytest.mark.parametrize('case', ['stale', 'gap', 'short', 'foreign', 'unknown', 'fetch_error', 'thin'])
def test_entries_fail_closed(case):
    d = flip_frame('le')
    R = mod()
    end = pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4)
    clock = (lambda: end.timestamp() + 90)
    fetch = None
    sym = 'AAVEUSDT'
    if case == 'stale': clock = (lambda: end.timestamp() + 4 * 3600 + 5)
    if case == 'gap': d = d.drop(index=len(d) - 50).reset_index(drop=True)
    if case == 'short': d = d.iloc[-(R.R1_CONTIG_BARS - 1):].reset_index(drop=True)
    if case == 'foreign': sym = 'BTCUSDT'
    if case == 'unknown': sym = None
    if case == 'fetch_error':
        def fetch(*a): raise ConnectionError('x')
    if case == 'thin':
        def fetch(s, a, b): return h1(a, b - pd.Timedelta(hours=1), qv=10.0)
    ctx_for(R, d, sym=sym, fetch=fetch, clock=clock)
    raw = R.raw_signals(d)
    assert raw['le'].iloc[-1]                                     # the rule itself flips on this row
    out = sig(R, d)
    assert not out['le'].any() and not out['se'].any()
    why = {'stale': 'stale', 'gap': 'gapped', 'short': 'history', 'foreign': 'frozen 163', 'unknown': 'unknown',
           'fetch_error': 'unavailable', 'thin': 'median'}[case]
    assert why in R.LAST_GATE[sym or '?']['reason'] and R.LAST_GATE[sym or '?']['ok'] is False
    np.testing.assert_array_equal(out['lx'], raw['lx'].values)   # exits never gated
    np.testing.assert_array_equal(out['sx'], raw['sx'].values)
    if case == 'fetch_error':
        assert not R._D01_CACHE                                    # a failed fetch is retried next call, not cached


def test_fetch_budget_per_decision_bar(monkeypatch):
    d = flip_frame('le')
    R = mod()
    assert R.MAX_H1_FETCHES_PER_BAR >= len(R.SYMBOLS)          # every frozen coin can be checked on a burst bar
    monkeypatch.setattr(R, 'MAX_H1_FETCHES_PER_BAR', 5)
    n = []
    ctx_for(R, d, fetch=lambda s, a, b: n.append(s) or h1(a, b - pd.Timedelta(hours=1)))
    syms = list(R.SYMBOLS)[:R.MAX_H1_FETCHES_PER_BAR + 3]
    oks = []
    for s in syms:
        R._CTX['resolve'] = (lambda x, s=s: s)
        oks.append(bool(sig(R, d)['le'][-1]))
    assert len(n) == R.MAX_H1_FETCHES_PER_BAR and oks == [True] * R.MAX_H1_FETCHES_PER_BAR + [False] * 3


# ---------------------------------------------------------------- overlay + engine receipt
import r6_paper_overlay as OV                                    # noqa: E402


class Resp:
    def __init__(self, w): self.headers = {'X-MBX-USED-WEIGHT-1M': str(w)}


def test_weight_pacer_waits_for_next_minute_only_above_soft_limit():
    now = [600.0]; slept = []; w = [100]
    c = type('C', (), {})()
    c._once = lambda *a, **k: (Resp(w[0]), {})
    OV.install_weight_pacer(c, soft_limit=1200, clock=lambda: now[0], sleep=lambda s: slept.append(s) or now.__setitem__(0, now[0] + s))
    c._once('GET', 'u', {}, False); assert not slept
    w[0] = 1250; c._once('GET', 'u', {}, False); assert not slept     # learns 1250 used
    now[0] += 10; c._once('GET', 'u', {}, False); assert len(slept) == 1 and 49 < slept[0] <= 50.5
    OV.install_weight_pacer(c); assert c._r6_paced                         # idempotent


def test_panel_exact_list_exception_is_bounded_to_ex14():
    import app
    orig_c, orig_v = app.clean_symbols, app.validate_sleeve
    try:
        R = mod()
        R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda d: None, lambda *a: None)
        with pytest.raises(ValueError): app.validate_sleeve(OV.sleeve(False), 0)   # the hazard: stock panel refuses 163
        OV.allow_exact_list(app); OV.allow_exact_list(app)                          # idempotent
        v = app.validate_sleeve(OV.sleeve(False), 0)                                # the whole panel slot check passes
        assert v['symbols'] == list(R.SYMBOLS) and v['tf'] == '4h' and v['max_pos'] == 1 and v['enabled'] is False
        with pytest.raises(ValueError): app.clean_symbols(list(R.SYMBOLS))         # outside EX14 validation: refused
        for bad in (list(R.SYMBOLS)[:-1] + ['ICPUSDT'], list(R.SYMBOLS)[:81], list(reversed(R.SYMBOLS))):
            with pytest.raises(ValueError): app.validate_sleeve(dict(OV.sleeve(False), symbols=bad), 0)
        with pytest.raises(ValueError): app.validate_sleeve(dict(OV.sleeve(False), id='EX01'), 0)
        with pytest.raises(ValueError): app.validate_sleeve(dict(OV.sleeve(False), key='ema_mom'), 0)
        assert app.validate_sleeve(dict(OV.sleeve(False), id='EX01', key='ema_mom', symbols=['BTCUSDT']), 0)['symbols'] == ['BTCUSDT']
        assert app.clean_symbols('all') == 'all'
        OV.restore_panel(app)
        assert app.clean_symbols is orig_c and app.validate_sleeve is orig_v
    finally:
        app.clean_symbols, app.validate_sleeve = orig_c, orig_v


def fresh_time(ago=60):
    """A decision bar whose close was `ago` seconds before now (as the engine's sg['time'] string)."""
    import time as _t
    return str(pd.Timestamp(int(_t.time()) - 14400 - ago, unit='s'))


class DataX:
    """A separate public-data client (as in production, where engine.data is not engine.trade)."""
    def __init__(self): self.calls = []
    def _once(self, *a, **k): self.calls.append(a); return Resp(0), {}
    def klines(self, *a, **k): return []


def a4_engine(**kw):
    import test_safety as TS
    import engine as E
    R = mod()
    S_ = dict(CAPITAL_CAP=4000.0, MAX_LEVERAGE=10, PAPER_BOOKS=False, TRUE_BOOKS=False, UNIVERSE=['BTCUSDT', 'ETHUSDT'],
              SYMBOLS_ON={'BTCUSDT': True, 'ETHUSDT': True})
    S_.update(kw)
    e, tmp = TS.mk_engine(**S_)
    e.trade.base = 'https://demo-fapi.binance.com'
    e.trade.balance = 5000.0
    e.trade._once = lambda *a, **k: (Resp(0), {})                 # pacer target (the fake has no HTTP layer)
    for s in R.SYMBOLS:
        e.rules[s] = dict(step=0.001, min_qty=0.001, tick=0.0001, min_notional=5.0)
        e.trade.mark.setdefault(s, 100.0)
    return e, E, R


@pytest.mark.parametrize('knob,val,msg', [
    ('base', 'https://fapi.binance.com', 'origin'), ('PAPER_BOOKS', True, 'book mode'), ('TRUE_BOOKS', True, 'book mode'),
    ('ENTRY_ORDER', 'maker', 'maker'), ('PRESET', 'breadth_rotation', 'rotation'), ('MODE', 'live', 'not PAPER'),
    ('live', True, 'not PAPER')])
def test_overlay_install_refuses_bad_settings(knob, val, msg):
    e, E, R = a4_engine()
    if knob == 'base': e.trade.base = val
    elif knob == 'MODE': e.cfg = dict(e.cfg, MODE=val)
    elif knob == 'live': e.live = val
    else: e.S[knob] = val
    with pytest.raises(OV.R6InstallError, match=msg): OV.install(e, pace=False)
    assert R.KEY not in S.STRATEGIES


def test_overlay_install_missing_rules_named_and_ok_path():
    e, E, R = a4_engine()
    del e.rules['ZRXUSDT']; del e.rules['1000BONKUSDT']
    with pytest.raises(OV.R6InstallError, match='1000BONKUSDT, ZRXUSDT'): OV.install(e, pace=False)
    assert R.KEY not in S.STRATEGIES
    e.rules['ZRXUSDT'] = e.rules['1000BONKUSDT'] = dict(step=0.001, min_qty=0.001, tick=0.0001, min_notional=5.0)
    e.S['SYMBOLS_ON']['AAVEUSDT'] = False
    with pytest.raises(OV.R6InstallError, match='order client'): OV.install(e)      # never pace the order client
    e.data = DataX()
    rep = OV.install(e)
    assert rep['missing_rules'] == [] and rep['switched_off'] == ['AAVEUSDT'] and rep['registered'] == R.KEY
    assert S.STRATEGIES[R.KEY] is R._PAPER_ENTRY and rep['paced'] is True          # paced before any slot exists
    assert not getattr(e.trade, '_r6_paced', False)


def test_saved_slot_without_install_keeps_other_slots_cycling():
    """Restart with EX14 saved but the install failing: an exits-only entry keeps every slot's 4h cycle working."""
    d = flip_frame('le')                                          # (reloads the module: before anything is set up)
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    e.S['SLEEVES'] = [E.sleeve('EX01', 'ema_mom', 1 / 12, 0.024, 1, ['BTCUSDT'])]
    OV.add_sleeve(e)
    with pytest.raises(OV.R6InstallError): OV.uninstall(e)                         # rollback order enforced
    R.unregister()
    e2 = E.Engine(dict(MODE='paper', API_KEY='k' * 16, API_SECRET='s' * 16), e.dir)
    assert [x['id'] for x in e2.S['SLEEVES']] == ['EX01', 'EX14']
    e2.data = e2.trade; e2.trade.base = 'https://demo-fapi.binance.com'; e2.connect()   # rules lack the 163 -> install fails
    with pytest.raises(KeyError):                                 # the hazard: compute_signals calls exactly this
        S.signals(R.KEY, d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False)
    with pytest.raises(OV.R6InstallError, match='without a testnet rule'): OV.install(e2, pace=False)
    entry = S.STRATEGIES[R.KEY]
    assert entry is R._EXITS_ONLY and entry['mgmt'] == {'stop_atr': 3.0, 'max_bars': R.BACKSTOP_BARS}
    out = S.signals(R.KEY, d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False)
    assert not out['le'].any() and not out['se'].any()
    np.testing.assert_array_equal(out['lx'], R.raw_signals(d)['lx'].values)
    for s in R.SYMBOLS: e2.rules[s] = dict(step=0.001, min_qty=0.001, tick=0.0001, min_notional=5.0)
    OV.install(e2, pace=False)                                                    # upgraded to the PAPER entry
    assert S.STRATEGIES[R.KEY] is R._PAPER_ENTRY and R._EXITS_ONLY is None


def test_slot_add_enable_remove():
    e, E, R = a4_engine()
    with pytest.raises(OV.R6InstallError): OV.add_sleeve(e)                     # install first
    OV.install(e, pace=False)
    e.S['SLEEVES'] = [E.sleeve(f'EX{i:02d}', 'ema_mom', 1 / 12, 0.024, 1, ['BTCUSDT'], enabled=i <= 8) for i in range(1, 14)]
    before = [dict(x) for x in e.S['SLEEVES']]
    for x in e.S['SLEEVES'][8:13]: x['enabled'] = True
    with pytest.raises(OV.R6InstallError, match='100%'): OV.add_sleeve(e, enabled=True)
    for x in e.S['SLEEVES'][8:13]: x['enabled'] = False
    sl = OV.add_sleeve(e)
    assert sl['enabled'] is False and e.S['SLEEVES'][:13] == before and e.S['SLEEVES'][13]['symbols'] == list(R.SYMBOLS)
    assert OV.add_sleeve(e) == e.S['SLEEVES'][13] and len(e.S['SLEEVES']) == 14                  # idempotent
    saved = json.load(open(e.F['settings'], encoding='utf-8'))
    assert [x['id'] for x in saved['SLEEVES']][-1] == 'EX14'
    OV.set_enabled(e, True); assert e.S['SLEEVES'][13]['enabled'] is True
    for x in e.S['SLEEVES'][8:13]: x['enabled'] = True                                     # 13/12 enabled -> refused
    OV.set_enabled(e, False)
    with pytest.raises(OV.R6InstallError, match='100%'): OV.set_enabled(e, True)
    for x in e.S['SLEEVES'][8:13]: x['enabled'] = False
    e.state['lots']['k'] = dict(sleeve='EX14', symbol='AAVEUSDT')
    with pytest.raises(OV.R6InstallError, match='holds'): OV.remove_sleeve(e)
    del e.state['lots']['k']
    OV.remove_sleeve(e); assert [x['id'] for x in e.S['SLEEVES']] == [x['id'] for x in before]
    e.S['SLEEVES'].append(dict(OV.sleeve(), risk=0.05))
    with pytest.raises(OV.R6InstallError, match='different'): OV.add_sleeve(e)


def test_explicit_list_bypasses_universe_and_keeps_frozen_order():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    sl = OV.sleeve(True)
    assert e.sleeve_symbols(sl, include_off=True) == list(R.SYMBOLS)        # UNIVERSE is only BTC/ETH here
    del e.rules['AAVEUSDT']                                                  # the engine itself filters by rules ...
    assert 'AAVEUSDT' not in e.sleeve_symbols(sl, include_off=True)
    assert OV.admission_report(e)['missing_rules'] == ['AAVEUSDT']            # ... so the report names it


@pytest.mark.parametrize('atr,cap_binds', [(2.0, False), (0.01, True)])
def test_sizing_receipt_through_engine_open_lot(atr, cap_binds):
    """Receipt: the real engine open_lot on account-4 basis (CAPITAL_CAP 4000, MAX_LEVERAGE 10, slot share 1/12, risk
    0.024, no books) sizes EX14 at $8.00 risk and caps notional at $3,333.33."""
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    sl = OV.sleeve(True)
    e.S['SLEEVES'] = [sl]
    eq = e.equity()
    assert eq == pytest.approx(4000.0)
    sg = dict(close=100.0, atr=atr, time=fresh_time(), le=True, se=False, lx=False, sx=False)
    assert e.open_lot(sl, 'AAVEUSDT', 'LONG', sg, None, eq) is True, e.last_skip
    lot = next(iter(e.state['lots'].values()))
    assert lot['sleeve'] == 'EX14' and lot['risk_usd'] == pytest.approx(8.0)
    notional = lot['qty'] * lot['avg']
    if cap_binds:
        assert notional == pytest.approx(3333.33, abs=0.1) and notional <= 3333.34
    else:
        assert lot['qty'] == pytest.approx(8.0 / (3 * atr), abs=1e-3)
        assert lot['stop'] == pytest.approx(100.0 - 3 * atr, abs=0.01)
    assert lot.get('book') in (None, '')
    assert lot['mgmt'].get('max_bars') == R.BACKSTOP_BARS and lot['mgmt'].get('stop_atr') == 3.0 and not lot['mgmt'].get('tp_r')


def test_engine_compute_signals_resolves_symbol_and_emits_ex14_entry():
    """Whole live path: engine.candles (fake public klines) -> compute_signals -> S.signals -> R.signal -> resolver by
    cache identity -> D01 1h fetch through engine.data.klines -> EX14|AAVEUSDT le on the decision row only."""
    d = flip_frame('le')
    e, E, R = a4_engine()
    rows = [[int(t.value // 10**6), o, h, l, c, v, int(t.value // 10**6) + 14400000 - 1, v * c]
            for t, o, h, l, c, v in zip(d.t, d.o, d.h, d.l, d.c, d.v)]
    end = pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4)
    seen = []

    def klines(sym, tf, limit=1500, end_time=None, start_time=None):
        seen.append((sym, tf, limit, start_time, end_time))
        if tf == '4h': return [list(r) for r in rows[-limit:]]
        hh = h1(pd.Timestamp(start_time, unit='ms'), pd.Timestamp(end_time + 1, unit='ms') - pd.Timedelta(hours=1), qv=1e6)
        return [[int(t.value // 10**6), 1, 1, 1, 1, 1, int(t.value // 10**6) + 3599999, q] for t, q in zip(hh.t, hh.qv)]
    e.data = type('D', (), {})(); e.data.klines = klines
    OV.install(e, pace=False)
    R._CTX['clock'] = lambda: end.timestamp() + 90
    e.S['SLEEVES'] = [OV.sleeve(True)]
    sigs, frames = e.compute_signals('4h', ['AAVEUSDT', 'ZRXUSDT'])
    g = sigs['EX14|AAVEUSDT']
    assert g['le'] is True and g['se'] is False and g['lx'] is False
    assert 'EX14|ZRXUSDT' in sigs                                            # same fake rows, its own cached frame object
    assert R.LAST_GATE['AAVEUSDT']['ok'] and R.LAST_GATE['ZRXUSDT']['ok']   # each frame resolved to its own symbol
    assert frames['AAVEUSDT'] is e._kc[('AAVEUSDT', '4h')][0]
    assert g['atr'] == pytest.approx(float(frames['AAVEUSDT'].atr.iloc[-1]))
    h1calls = [x for x in seen if x[1] == '1h']
    assert len(h1calls) == 2 and {x[0] for x in h1calls} == {'AAVEUSDT', 'ZRXUSDT'}
    assert all(x[2] == R.H1_LIMIT and x[4] == int(end.value // 10**6) - 1 for x in h1calls)


# ---------------------------------------------------------------- engine cycle: exits and data behaviour
def _opened_ex14(e, OV_):
    sl = OV_.sleeve(True); e.S['SLEEVES'] = [sl]
    sg = dict(close=100.0, atr=2.0, time=fresh_time(), le=True, se=False, lx=False, sx=False)
    assert e.open_lot(sl, 'AAVEUSDT', 'LONG', sg, None, e.equity()) is True, e.last_skip
    return next(iter(e.state['lots']))


def _cycle(e, sig):
    e.compute_signals = lambda tf, syms, extra=(): ({'EX14|AAVEUSDT': dict(sig)} if tf == '4h' else {}, {})
    e.cycle('4h')


BASE_SIG = dict(le=False, se=False, lx=False, sx=False, close=100.0, atr=2.0, time='2026-10-21 00:00:00', vol_rank=0.5)


@pytest.mark.parametrize('bars,closes', [(121.0, False), (123.9, False), (124.0, True)])
def test_engine_max_bars_is_only_a_backstop(bars, closes):
    # the engine's wall-clock max_bars never fires before the canonical deadline (close of entry+120 = 121 bars after the
    # entry bar's open); it only catches a lot whose canonical exit signal could not be computed
    from datetime import timedelta
    import engine as E
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    k = _opened_ex14(e, OV)
    e.state['lots'][k]['opened'] = (E.now_utc() - timedelta(seconds=bars * 14400)).isoformat(timespec='seconds')
    _cycle(e, BASE_SIG)
    assert (k not in e.state['lots']) == closes


def test_cycle_exit_signal_closes_and_short_ignores_long_exit():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    k = _opened_ex14(e, OV)
    _cycle(e, dict(BASE_SIG, sx=True))                       # a short-exit flag never closes a long
    assert k in e.state['lots']
    _cycle(e, dict(BASE_SIG, lx=True))
    assert k not in e.state['lots']


def test_disabled_slot_still_downloads_all_163_coins_every_cycle():
    """Why no EX14 slot - even switched off - goes into settings before canary: the engine fetches candles for every
    slot's coins, enabled or not (measured here with the stock engine, no overlay pacing)."""
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    n = []
    e.data = type('D', (), {})(); e.data.klines = lambda sym, tf, limit=1500, **k: n.append((sym, tf, limit)) or []
    e.S['SLEEVES'] = [OV.sleeve(False)]
    try: e.cycle('4h')
    except Exception: pass
    got = {s for s, tf, lim in n if tf == '4h' and lim == 1500}
    assert set(R.SYMBOLS) <= got                              # 163 x weight-10 requests per 4h close, slot OFF


def test_d01_future_candle_after_cutoff_is_ignored_not_used():
    R = mod()
    end = DEC + pd.Timedelta(hours=3)
    h = h1('2026-09-18', end + pd.Timedelta(hours=2))            # two candles past the cutoff
    assert R.d01_check(h, DEC)[0]
    assert R.d01_check(h1('2026-09-18', end, drop=[end - pd.Timedelta(hours=1)]), DEC)[0] is False   # gap today


def test_weight_pacer_resets_each_minute():
    now = [600.0]; slept = []
    c = type('C', (), {})()
    c._once = lambda *a, **k: (Resp(2000), {})
    OV.install_weight_pacer(c, soft_limit=1200, clock=lambda: now[0], sleep=lambda s: slept.append(s))
    c._once('GET', 'u', {}, False)
    now[0] = 661.0                                                # next minute: the old count no longer applies
    c._once('GET', 'u', {}, False)
    assert not slept


@pytest.mark.parametrize('late,ok', [(15, True), (1199, True), (1201, False), (3 * 3600, False)])
def test_no_late_entries_after_the_decision_close(late, ok):
    d = flip_frame('le')
    R = mod()
    end = pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4)
    ctx_for(R, d, clock=lambda: end.timestamp() + late)
    assert bool(sig(R, d)['le'][-1]) == ok


def test_guard_rechecked_every_decision_and_off_slot_spends_no_fetch():
    d = flip_frame('le')
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    e.S['SLEEVES'] = [OV.sleeve(True)]
    n = []
    end = pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4)
    R._CTX.update(resolve=lambda x: 'AAVEUSDT', clock=lambda: end.timestamp() + 60,
                  fetch_1h=lambda s, a, b: n.append(s) or h1(a, b - pd.Timedelta(hours=1)))
    e.S['PAPER_BOOKS'] = True                                     # switched on through the panel after install
    assert not sig(R, d)['le'][-1] and 'book' in R.LAST_GATE['AAVEUSDT']['reason']
    e.S['PAPER_BOOKS'] = False; e.S['SLEEVES'] = [OV.sleeve(False)]
    assert not sig(R, d)['le'][-1] and R.LAST_GATE['AAVEUSDT']['reason'] == 'EX14 switched off' and not n
    e.S['SLEEVES'] = [OV.sleeve(True)]
    assert sig(R, d)['le'][-1] and n == ['AAVEUSDT']


def test_d01_refuses_duplicate_or_unsorted_candles():
    R = mod()
    h = h1('2026-09-18', DEC + pd.Timedelta(hours=3))
    assert not R.d01_check(pd.concat([h, h.iloc[[100]]], ignore_index=True), DEC)[0]
    assert not R.d01_check(h.iloc[::-1].reset_index(drop=True), DEC)[0]
    old = pd.Timestamp(DEC.floor('D') - pd.Timedelta(days=31))       # outside the run and the 30-day median window
    dup = pd.concat([h, h[h.t == old]], ignore_index=True).sort_values('t', kind='stable').reset_index(drop=True)
    assert R.d01_check(h[h.t >= old].reset_index(drop=True), DEC)[0]
    assert not R.d01_check(dup[dup.t >= old].reset_index(drop=True), DEC)[0]


def test_h1_fetcher_keeps_only_closed_candles_in_the_window():
    e = type('E', (), {})(); e.data = type('D', (), {})()
    t0 = pd.Timestamp('2026-10-20 00:00')
    rows = [[int((t0 + pd.Timedelta(hours=i)).value // 10**6), 0, 0, 0, 0, 0,
             int((t0 + pd.Timedelta(hours=i + 1)).value // 10**6) - 1, float(i)] for i in range(10)]
    e.data.klines = lambda *a, **k: rows
    now = (t0 + pd.Timedelta(hours=5)).timestamp() + 1            # candles 0..4 closed, 5.. open
    f = OV.h1_fetcher(e, clock=lambda: now)
    got = f('X', t0 + pd.Timedelta(hours=1), t0 + pd.Timedelta(hours=8))
    assert list(got.qv) == [1.0, 2.0, 3.0, 4.0]


def test_registration_refuses_an_altered_symbol_list(monkeypatch):
    R = mod()
    monkeypatch.setattr(R, 'SYMBOLS', R.SYMBOLS[:-1] + ('ZZZUSDT',))       # still 163, still sorted: only the hash differs
    with pytest.raises(ValueError, match='altered'):
        R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda d: None, lambda *a: None)


# ---------------------------------------------------------------- Codex review r1: canonical deadline
def frame_rows(t_last, n=900):
    t = pd.date_range(end=pd.Timestamp(t_last), periods=n, freq='4h')
    c = np.full(n, 100.0)
    return pd.DataFrame(dict(t=t, o=c, h=c, l=c, c=c, v=np.ones(n)))


@pytest.mark.parametrize('k,due', [(118, False), (119, False), (120, True), (121, True), (127, True)])
def test_canonical_deadline_from_the_entry_bar(k, due):
    """Research: exit when i - i_entry >= 120 -> at the close of row entry+120, whatever the cycle delays; a late or
    restarted cycle (row entry+121, +127) exits at its first chance."""
    R = mod()
    entry = pd.Timestamp('2026-06-01 04:00')
    d = frame_rows(entry + k * R.STEP)
    for side, ex in (('LONG', 'lx'), ('SHORT', 'sx')):
        R._CTX.update(resolve=lambda x: 'AAVEUSDT', lot_entry=lambda s, side=side: (side, entry))
        lx, sx = R.deadline_exits(d, pd.Series(False, index=d.index), pd.Series(False, index=d.index))
        got = {'lx': lx, 'sx': sx}
        assert bool(got[ex].iloc[-1]) == due
        assert not got['sx' if ex == 'lx' else 'lx'].any()           # only the lot's own side
    R._CTX.update(lot_entry=lambda s: None)
    lx, sx = R.deadline_exits(d, pd.Series(False, index=d.index), pd.Series(False, index=d.index))
    assert not lx.any() and not sx.any()


def test_backstop_can_never_precede_the_canonical_deadline():
    R = mod()
    # worst case: fill submitted ENTRY_WINDOW_S after the entry bar opened, every later cycle exactly on time
    for k in range(0, 121):
        wall = (k + 1) * 14400 - R.ENTRY_WINDOW_S
        assert wall / 14400 < R.BACKSTOP_BARS, k


def test_lot_entry_uses_the_recorded_bar_else_the_fill_bar():
    e, E, R = a4_engine()
    f = OV.lot_entry(e)
    e.state['lots']['a'] = dict(sleeve='EX14', symbol='AAVEUSDT', side='LONG', opened='2026-06-01T04:19:00+00:00')
    assert f('AAVEUSDT') == ('LONG', pd.Timestamp('2026-06-01 04:00'))
    e.state['lots']['a']['r6_entry_bar'] = '2026-06-01T04:00:00'
    e.state['lots']['a']['opened'] = '2026-06-01T07:59:00+00:00'
    assert f('AAVEUSDT') == ('LONG', pd.Timestamp('2026-06-01 04:00'))
    assert f('ZRXUSDT') is None
    e.state['lots']['b'] = dict(sleeve='EX13', symbol='ZRXUSDT', side='SHORT', opened='2026-06-01T04:19:00+00:00')
    assert f('ZRXUSDT') is None                                          # another slot's lot is not R6's


# ---------------------------------------------------------------- Codex review r1: reserved, immutable slot
BAD_SLOTS = [dict(id='EX15'), dict(symbols=['AAVEUSDT']), dict(risk=0.048), dict(max_pos=3), dict(share=0.2),
             dict(sides='long'), dict(mgmt={'tp_r': 3}), dict(kelly={'frac': 1.0}), dict(hours=[0, 4])]


@pytest.mark.parametrize('chg', BAD_SLOTS)
def test_panel_refuses_any_r6_slot_but_the_frozen_ex14(chg):
    import app
    orig_c, orig_v = app.clean_symbols, app.validate_sleeve
    try:
        R = mod()
        R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda d: None, lambda *a: None)
        OV.allow_exact_list(app)
        assert app.validate_sleeve(OV.sleeve(True), 0)['enabled'] is True
        with pytest.raises(ValueError): app.validate_sleeve(dict(OV.sleeve(True), **chg), 0)
        with pytest.raises(ValueError): app.validate_sleeve(dict(id='EX14', key='ema_mom', share=0.05, risk=0.01, max_pos=1, symbols=['BTCUSDT']), 0)
    finally:
        app.clean_symbols, app.validate_sleeve = orig_c, orig_v


@pytest.mark.parametrize('chg', BAD_SLOTS)
def test_entry_guard_refuses_a_changed_or_extra_r6_slot(chg):
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    e.equity()
    g = OV.entry_guard(e)
    e.S['SLEEVES'] = [OV.sleeve(True)]
    assert g() is None
    if chg.get('id') == 'EX15':
        e.S['SLEEVES'] = [OV.sleeve(True), dict(OV.sleeve(True), id='EX15')]
    else:
        e.S['SLEEVES'] = [dict(OV.sleeve(True), **chg)]
    assert g()


def test_risk_guard_includes_upward_multipliers_and_equity():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    sl = OV.sleeve(True); e.S['SLEEVES'] = [sl]
    assert OV.risk_problem(e, sl, 4000.0) is None
    e.governor_mult = lambda: 2.0                                         # governor can be > 1 (capped at 2)
    assert 'risk' in OV.risk_problem(e, sl, 4000.0)
    e.governor_mult = lambda: 1.0
    e.kelly_mult = lambda s: 1.5
    assert 'risk' in OV.risk_problem(e, sl, 4000.0)
    e.kelly_mult = lambda s: 1.0
    assert 'risk' in OV.risk_problem(e, sl, 4400.0)                      # compounding above the $4,000 basis
    assert OV.risk_problem(e, sl, 3500.0) is None                         # lower equity: smaller risk is fine
    e.S['MAX_LEVERAGE'] = 20
    assert 'notional' in OV.risk_problem(e, sl, 4000.0)
    assert OV.risk_problem(e, sl, None) == 'sizing equity unknown'


# ---------------------------------------------------------------- Codex review r1: failed reinstall downgrades
def test_failed_reinstall_downgrades_an_active_registration_to_exits_only():
    d = flip_frame('le')
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    assert S.STRATEGIES[R.KEY] is R._PAPER_ENTRY
    del e.rules['ZRXUSDT']
    with pytest.raises(OV.R6InstallError, match='ZRXUSDT'): OV.install(e, pace=False)
    assert S.STRATEGIES[R.KEY] is R._EXITS_ONLY and R._PAPER_ENTRY is None and R._CTX['fetch_1h'] is None
    out = S.signals(R.KEY, d, dict(btc_down=np.zeros(len(d), bool)), mask_sides=False)
    assert not out['le'].any() and not out['se'].any()
    np.testing.assert_array_equal(out['lx'], R.raw_signals(d)['lx'].values)


# ---------------------------------------------------------------- Codex review r1: submission window at the order
def test_open_lot_wrapper_refuses_late_entry_before_any_order_and_records():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    sl = OV.sleeve(True); e.S['SLEEVES'] = [sl]
    eq = e.equity()
    n_open = e.trade.calls.count('open')
    sg = dict(close=100.0, atr=2.0, time=fresh_time(ago=R.ENTRY_WINDOW_S + 30), le=True, se=False, lx=False, sx=False)
    assert e.open_lot(sl, 'AAVEUSDT', 'LONG', sg, None, eq) is False
    assert 'late entry' in e.last_skip and e.trade.calls.count('open') == n_open and not e.state['lots']
    sg = dict(sg, time=fresh_time(ago=30))
    assert e.open_lot(sl, 'AAVEUSDT', 'LONG', sg, None, eq) is True
    lot = next(iter(e.state['lots'].values()))
    assert lot['r6_entry_bar'] == (pd.Timestamp(sg['time']) + R.STEP).isoformat()
    rows = [json.loads(x) for x in open(os.path.join(e.dir, 'r6_entries.jsonl'), encoding='utf-8')]
    assert [r['result'] for r in rows] == ['refused', 'filled']
    assert rows[1]['slippage_bps'] == 0.0 and 25 <= rows[1]['attempt_latency_s'] <= 120 and rows[1]['risk_usd'] == 8.0
    assert 'submit_utc' not in rows[1] and 'latency_s' not in rows[1]
    assert rows[0]['send_utc'] is None and rows[0]['fill_utc'] is None           # refused: nothing was sent
    att, snd, fil = (pd.Timestamp(rows[1][k]) for k in ('attempt_utc', 'send_utc', 'fill_utc'))
    assert att <= snd and snd.floor('s') <= fil                          # attempt, then exchange send, then the fill
    assert rows[1]['attempt_latency_s'] <= rows[1]['send_latency_s']
    e.governor_mult = lambda: 2.0                                         # rechecked at the order, with the engine's eq
    e.state['lots'].clear()
    assert e.open_lot(sl, 'ZRXUSDT', 'LONG', sg, None, eq) is False and 'risk' in e.last_skip


def test_open_lot_wrapper_leaves_manual_and_other_slots_alone():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    other = E.sleeve('EX01', 'ema_mom', 1 / 12, 0.024, 1, ['BTCUSDT'])
    e.S['SLEEVES'] = [other, OV.sleeve(True)]
    stale = dict(close=100.0, atr=2.0, time='2020-01-01 00:00:00', le=True, se=False, lx=False, sx=False)
    assert e.open_lot(other, 'BTCUSDT', 'LONG', stale, None, e.equity()) is True        # not EX14: untouched
    assert not os.path.exists(os.path.join(e.dir, 'r6_entries.jsonl'))


def test_window_rechecked_after_a_slow_d01_fetch():
    d = flip_frame('le')
    R = mod()
    end = pd.Timestamp(d.t.iloc[-1]) + pd.Timedelta(hours=4)
    now = [end.timestamp() + 60]

    def slow(s, a, b):
        now[0] += R.ENTRY_WINDOW_S                                        # the fetch (pacer) took 20 minutes
        return h1(a, b - pd.Timedelta(hours=1))
    ctx_for(R, d, fetch=slow, clock=lambda: now[0])
    assert not sig(R, d)['le'][-1] and 'late entry' in R.LAST_GATE['AAVEUSDT']['reason']


def test_overlap_recorded_not_blocked():
    e, E, R = a4_engine()
    e.S['SLEEVES'] = [E.sleeve('EX13', 'exp_trend', 1 / 12, 0.024, 3, ['AAVEUSDT', 'ZRXUSDT', 'BTCUSDT']),
                      E.sleeve('EX01', 'ema_mom', 1 / 12, 0.024, 1, ['BTCUSDT'])]
    rep = OV.install(e, pace=False)
    ov = rep['notes']['overlap']
    assert list(ov['slots']) == ['EX13'] and ov['slots']['EX13']['coins'] == 2 and ov['slots']['EX13']['risk_usd'] == 8.0
    assert ov['worst_combined_risk_usd_one_coin'] == 16.0


def test_paper_signal_and_exits_only_both_apply_the_canonical_deadline():
    R = mod()
    entry = pd.Timestamp('2026-06-01 04:00')
    d = frame_rows(entry + 120 * R.STEP)
    d['c'] = np.linspace(300.0, 100.0, len(d))                           # a falling coin: the frozen rule keeps the short
    R.register_for_paper('paper', 'https://demo-fapi.binance.com', lambda x: 'AAVEUSDT', lambda *a: None,
                         lot_entry=lambda s: ('SHORT', entry))
    raw = R.raw_signals(d)
    assert not raw['sx'].iloc[-1]                                         # only the deadline can close it here
    out = sig(R, d)
    assert out['sx'][-1] and np.array_equal(out['lx'], raw['lx'].values)
    R.register_exits_only('test downgrade')
    out = sig(R, d)
    assert out['sx'][-1] and not out['le'].any() and not out['se'].any()
    d2 = d.iloc[:-1].reset_index(drop=True)
    assert not sig(R, d2)['sx'][-1]                                       # one row before the deadline: still held


def test_guard_itself_refuses_on_upward_multiplier():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    e.S['SLEEVES'] = [OV.sleeve(True)]
    e.equity()
    g = OV.entry_guard(e)
    assert g() is None
    e.governor_mult = lambda: 1.5
    assert 'risk' in g()


def test_wrapper_checks_risk_at_the_eq_the_engine_sizes_with():
    e, E, R = a4_engine()
    OV.install(e, pace=False)
    sl = OV.sleeve(True); e.S['SLEEVES'] = [sl]
    e.equity()                                                            # last_eq 4000: the guard alone would pass
    sg = dict(close=100.0, atr=2.0, time=fresh_time(), le=True, se=False, lx=False, sx=False)
    assert e.open_lot(sl, 'AAVEUSDT', 'LONG', sg, None, 4400.0) is False and 'risk' in e.last_skip


def test_install_refuses_a_bad_saved_r6_slot_and_keeps_exits():
    e, E, R = a4_engine()
    e.S['SLEEVES'] = [dict(OV.sleeve(False), max_pos=3)]
    with pytest.raises(OV.R6InstallError, match='max_pos'): OV.install(e, pace=False)
    assert S.STRATEGIES[R.KEY] is R._EXITS_ONLY


# ---------------------------------------------------------------- Codex review r2: fresh-process failed install
@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
@pytest.mark.parametrize('recorded', [True, False])
def test_fresh_failed_install_keeps_the_canonical_deadline(side, recorded):
    """Fresh process: nothing registered, a saved EX14 lot, one unrelated frozen symbol without a testnet rule. install()
    must refuse, register exits-only AND give it the read-only deadline context: the lot closes at entry + 120 bars
    (not at the 124-bar backstop) while the frozen regime still holds it, and never earlier."""
    R = mod()
    assert R._CTX['resolve'] is None and R._CTX['lot_entry'] is None and R.KEY not in S.STRATEGIES
    e, E, R = a4_engine()
    entry = pd.Timestamp('2026-06-01 04:00')
    lot = dict(sleeve='EX14', key_strategy=R.KEY, symbol='AAVEUSDT', side=side, qty=1.0, avg=100.0,
               opened=(entry + pd.Timedelta(minutes=7)).isoformat() + '+00:00')
    if recorded: lot['r6_entry_bar'] = entry.isoformat()
    e.state['lots']['EX14|AAVEUSDT|' + side] = lot
    del e.rules['ZRXUSDT']                                              # unrelated to the held coin
    with pytest.raises(OV.R6InstallError, match='ZRXUSDT'): OV.install(e, pace=False)
    assert S.STRATEGIES[R.KEY] is R._EXITS_ONLY and R._PAPER_ENTRY is None
    assert R._CTX['fetch_1h'] is None and R._CTX['guard'] is None          # no entry path
    assert callable(R._CTX['resolve']) and callable(R._CTX['lot_entry'])
    ex, other = ('lx', 'sx') if side == 'LONG' else ('sx', 'lx')
    for k, due in ((118, False), (119, False), (120, True), (121, True)):
        d = frame_rows(entry + k * R.STEP)
        d['c'] = np.linspace(100.0, 300.0, len(d)) if side == 'LONG' else np.linspace(300.0, 100.0, len(d))
        e._kc[('AAVEUSDT', '4h')] = (d, 0.0)                              # the engine's own cached frame object
        raw = R.raw_signals(d)
        assert not raw[ex].iloc[-1]                                     # the regime still holds the lot
        out = sig(R, d)
        assert bool(out[ex][-1]) == due, k
        assert not out['le'].any() and not out['se'].any()
        np.testing.assert_array_equal(out[other], raw[other].values)       # the other side is the frozen rule only
        want = raw[ex].values | (pd.to_datetime(d.t).values >= np.datetime64(entry + R.MAX_BARS * R.STEP))
        np.testing.assert_array_equal(out[ex], want)                    # deadline rows only, never an earlier one
    e._kc.clear()
    assert not sig(R, d)[ex][-1]                                         # an unresolvable frame: backstop only, no guess


def test_fresh_failed_install_without_any_r6_reference_registers_nothing():
    R = mod()
    e, E, R = a4_engine()
    del e.rules['ZRXUSDT']
    with pytest.raises(OV.R6InstallError, match='ZRXUSDT'): OV.install(e, pace=False)
    assert R.KEY not in S.STRATEGIES and R._CTX['resolve'] is None and R._CTX['lot_entry'] is None


def test_exits_only_context_is_replaced_only_by_given_callables():
    R = mod()
    a, b = (lambda d: 'A'), (lambda s: None)
    first = R.register_exits_only('first', resolve=a, lot_entry=b)
    assert R._CTX['resolve'] is a and R._CTX['lot_entry'] is b
    R.register_exits_only('again')                                       # nothing given: kept
    assert R._CTX['resolve'] is a and R._CTX['lot_entry'] is b
    c = lambda d: 'C'
    assert R.register_exits_only('again', resolve=c) is first is S.STRATEGIES[R.KEY]   # the same entry, not rebuilt
    assert R._CTX['resolve'] is c and R._CTX['lot_entry'] is b and R._CTX['fetch_1h'] is None
    R.unregister()
    S.STRATEGIES[R.KEY] = foreign = dict(name='someone else')
    try:
        assert R.register_exits_only('x', resolve=a) is foreign and R._CTX['resolve'] is None   # foreign: untouched
    finally:
        del S.STRATEGIES[R.KEY]
