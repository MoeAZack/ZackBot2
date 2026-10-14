"""VWAPREV runtime adapter (strategies.exp_vwaprev): parity with the REX-01 research module on synthetic tapes only.

No store, archive, holdout or network read: every candle is generated here or by tests/rex_synth.py (exact-z fixtures).
Covers: bar-for-bar signal parity with range_vwap_rev (indicators / entry_x1 / mean_close / invalidated) including the
frozen equality boundaries, fail-closed windows (holes, missing quote volume, non-4h step), the stop (always present,
correct side, 1.5 x the decision candle's TR-SMA14), never both entries on one candle, side masking, and the registry /
App.meta / validate_sleeve timeframe."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import engine as E
import feasibility as F
import strategies as S
import rex_synth as RS

RV = RS.RV
CFG = RV.PRIMARY
LOOK = CFG.lookback                                  # 48 candles per decision
KEY = 'exp_vwaprev'


# ------------------------------------------------------------------ helpers
def to_df(x):
    """A research Series (bars keyed by open ms) -> the engine's candle frame t,o,h,l,c,v,qv (sorted, holes dropped)."""
    bs = [x.bars[k] for k in sorted(x.bars)]
    return pd.DataFrame(dict(t=pd.to_datetime([b.open_ms for b in bs], unit='ms'), o=[b.open for b in bs],
                             h=[b.high for b in bs], l=[b.low for b in bs], c=[b.close for b in bs],
                             v=[b.volume for b in bs], qv=[b.quote_volume for b in bs]))


def research(x):
    """Research decisions per sorted bar index (None = the research read would fail: hole in the 48-bar window)."""
    keys = sorted(x.bars)
    out = []
    for i, k in enumerate(keys):
        if i < LOOK - 1:
            out.append(None); continue
        try:
            bars = RV.read(x, k + RV.TF, LOOK)
        except RV.Incomplete:
            out.append(None); continue
        ind = RV.indicators(CFG, bars)
        out.append(dict(le=RV.entry_x1(CFG, ind, 'long'), se=RV.entry_x1(CFG, ind, 'short'),
                        lx=RV.mean_close(ind, 'long') or RV.invalidated(ind),
                        sx=RV.mean_close(ind, 'short') or RV.invalidated(ind),
                        atr=None if ind.atr is None else float(ind.atr)))
    return out


def sig(df, params=None, mask=False, key=KEY):
    d = S.indicators(df.reset_index(drop=True))
    ctx = dict(btc_down=np.zeros(len(d), bool), rank=pd.Series(999.0, index=d.index),
               rank7=pd.Series(999.0, index=d.index), n_symbols=1)
    return S.signals(key, d, ctx, params, mask_sides=mask)


def assert_parity(x):
    df, ref = to_df(x), research(x)
    s = sig(df)
    for i, r in enumerate(ref):
        if r is None:
            assert not (s['le'][i] or s['se'][i] or s['lx'][i] or s['sx'][i]), i
            continue
        for col in ('le', 'se', 'lx', 'sx'):
            assert bool(s[col][i]) == r[col], (col, i)
        if r['atr'] is None:
            assert np.isnan(s['atr'][i])
        else:
            assert s['atr'][i] == r['atr'], i
    return s


def random_tape(seed, n=400, flat=False):
    """Random 4h OHLCV with a quote volume close to typical price x volume (research Series and engine frame)."""
    rng = np.random.default_rng(seed)
    r = rng.standard_t(4, n) * (0.004 if flat else 0.01)
    c = 100.0 * np.exp(np.cumsum(r) + 0.05 * np.sin(np.arange(n) / 5.0))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.004, n)) * c
    h, l = np.maximum(o, c) + w, np.minimum(o, c) - w
    v = np.round(1000.0 + 500 * rng.random(n), 3)
    qv = np.round((h + l + c) / 3 * v * (1 + rng.normal(0, 0.001, n)), 6)
    bars = {}
    for i in range(n):
        om = RS.H + i * RV.TF
        bars[om] = RS.P.Bar(om, round(o[i], 8), round(h[i], 8), round(l[i], 8), round(c[i], 8), float(v[i]),
                            float(qv[i]), om + RV.TF)
    return RV.Series(bars, dict(RS.RULE))


LE = RS.long_entry(70)                                # long X1 signal at bar 70 (z -2.5 -> -1.5, RG true)


# ------------------------------------------------------------------ parity with the research module
@pytest.mark.parametrize('side', RV.SIDES)
@pytest.mark.parametrize('vwap', [LE, RS.long_entry(70, up=4.0),                 # z_(i-1) == -2 exactly (entry)
                                  RS.long_entry(70, up=6.0, back=4.0),          # z_i == -2 exactly (no entry)
                                  {64: 101.5, 69: 105.0, 70: 103.5},            # RG drift == 1 ATR exactly (entry)
                                  {64: 101.5, 69: 105.0, 70: 103.6},            # RG broken (invalidation exit)
                                  {69: 105.0, 70: 100.0}],                      # close == VWAP (mean exit, no entry)
                         ids=['x1', 'zprev_eq_k', 'z_eq_k', 'rg_eq_1atr', 'rg_broken', 'at_mean'])
def test_parity_exact_fixtures(side, vwap):
    s = assert_parity(RS.build(n=90, side=side, vwap=vwap))
    if vwap is LE:
        col = 'le' if side == 'long' else 'se'
        assert s[col].any()


@pytest.mark.parametrize('seed', [1, 2, 3, 4, 5, 6])
def test_parity_random_tapes(seed):
    x = random_tape(seed, flat=seed % 2 == 0)
    s = assert_parity(x)
    assert (s['lx'] | s['sx']).any()


def test_parity_random_tapes_trade_both_sides():
    n_le = n_se = 0
    for seed in range(1, 13):
        s = sig(to_df(random_tape(seed, flat=True)))
        n_le += int(s['le'].sum()); n_se += int(s['se'].sum())
    assert n_le > 0 and n_se > 0


def test_mirror_symmetry():
    a, b = sig(to_df(RS.build(n=90, side='long', vwap=LE))), sig(to_df(RS.build(n=90, side='short', vwap=LE)))
    for x, y in (('le', 'se'), ('se', 'le'), ('lx', 'sx'), ('sx', 'lx')):
        assert (a[x] == b[y]).all(), x
    assert a['le'].any()


def test_causal_truncation():
    df = to_df(random_tape(3, flat=True))
    full = sig(df)
    for k in (60, 120, 200, 333, len(df) - 2):
        cut = sig(df.iloc[:k + 1])
        for col in ('le', 'se', 'lx', 'sx'):
            assert (cut[col] == full[col][:k + 1]).all(), (col, k)


# ------------------------------------------------------------------ fail closed
@pytest.mark.parametrize('side', RV.SIDES)
def test_hole_in_window_blocks_entry_and_indicator_exit(side):
    x = RS.build(n=90, side=side, vwap=LE, holes=(60,))
    s = assert_parity(x)
    df = to_df(x)
    after = df.index[df.t >= pd.Timestamp(RS.ms(60), unit='ms')]          # bars 61 .. 61 + 47 read across the hole
    blocked = after[:LOOK]
    assert not (s['le'][blocked] | s['se'][blocked] | s['lx'][blocked] | s['sx'][blocked]).any()
    assert not s['le'].any() and not s['se'].any()                       # the bar-70 entry is inside the hole window


def test_missing_quote_volume_fails_closed():
    df = to_df(RS.build(n=90, vwap=LE))
    s = sig(df.drop(columns='qv'))
    assert not any(s[c].any() for c in ('le', 'se', 'lx', 'sx'))
    df2 = df.copy(); df2.loc[df2.index[-25], 'qv'] = np.nan               # the engine's replay feed: NaN quote volume
    s2 = sig(df2)
    assert not (s2['le'][-25:].any() or s2['lx'][-25:].any())


def test_non_4h_step_fails_closed():
    df = to_df(RS.build(n=90, vwap=LE))
    df['t'] = pd.date_range('2026-01-01', periods=len(df), freq='1h')
    s = sig(df)
    assert not any(s[c].any() for c in ('le', 'se', 'lx', 'sx'))


def test_zero_volume_window_has_no_signal():
    df = to_df(RS.build(n=90, vwap=LE))
    df['v'] = 0.0; df['qv'] = 0.0
    s = sig(df)
    assert not any(s[c].any() for c in ('le', 'se', 'lx', 'sx'))


# ------------------------------------------------------------------ one side per candle, stops
def test_never_both_entries_on_one_candle():
    for seed in range(1, 13):
        s = sig(to_df(random_tape(seed, flat=seed % 3 == 0)))
        assert not (s['le'] & s['se']).any()
        assert not (s['le'] & s['lx']).any() and not (s['se'] & s['sx']).any()   # an entry never exits itself
    # the _one_side guard: even a (hypothetical) double raw signal yields neither entry
    both = S._one_side(dict(le=pd.Series([True, True]), se=pd.Series([True, False]), lx=pd.Series([False] * 2),
                            sx=pd.Series([False] * 2)))
    assert list(both['le']) == [False, True] and list(both['se']) == [False, False]


@pytest.mark.parametrize('side', RV.SIDES)
def test_stop_present_correct_side_and_tr_sma14(side):
    x = RS.build(n=90, side=side, vwap=LE)
    df, ref = to_df(x), research(x)
    s = sig(df)
    g = S.merge_mgmt(KEY)
    col, sd = ('le', 1) if side == 'long' else ('se', -1)
    hits = np.flatnonzero(s[col])
    assert len(hits)
    for i in hits:
        px = float(df.c.iloc[i])
        atr = E._sig_atr(dict(atr=s['atr'][:i + 1]), SimpleNamespace(atr=999.0))
        assert atr == ref[i]['atr'] == 2.0                                     # TR-SMA14 of the decision, not Wilder
        z = F.risk_qty(g, 10.0, px, atr, sd)
        assert z['qty'] > 0 and z['R'] == pytest.approx(1.5 * atr)
        assert (z['stop'] < px) if sd == 1 else (z['stop'] > px)


def test_random_entries_always_have_a_positive_stop_atr():
    for seed in range(1, 13):
        s = sig(to_df(random_tape(seed, flat=True)))
        for i in np.flatnonzero(s['le'] | s['se']):
            assert np.isfinite(s['atr'][i]) and s['atr'][i] > 0


def test_sig_atr_falls_back_to_engine_atr():
    d = SimpleNamespace(atr=3.25)
    assert E._sig_atr(dict(atr=np.array([1.0, np.nan])), d) == 3.25
    assert E._sig_atr(dict(atr=np.array([0.0])), d) == 3.25
    assert E._sig_atr(dict(le=np.array([True])), d) == 3.25                 # every other strategy: unchanged
    assert E._sig_atr(dict(atr=np.array([np.nan, 1.5])), d) == 1.5


def test_engine_candles_keep_quote_volume():
    t0 = 1_700_006_400_000
    rows = [[t0 + i * 14_400_000, '1', '2', '0.5', '1.5', '10', t0 + (i + 1) * 14_400_000 - 1, '15', 0, 0, 0, 0]
            for i in range(3)]
    fake = SimpleNamespace(_kc={}, data=SimpleNamespace(klines=lambda *a, **k: rows))
    df = E.Engine.candles(fake, 'BTCUSDT', '4h')
    assert list(df.qv) == [15.0] * 3 and {'t', 'o', 'h', 'l', 'c', 'v', 'atr'} <= set(df.columns)
    fake7 = SimpleNamespace(_kc={}, data=SimpleNamespace(klines=lambda *a, **k: [r[:7] for r in rows]))
    assert E.Engine.candles(fake7, 'BTCUSDT', '4h').qv.isna().all()        # replay feed: no quote volume -> NaN


# ------------------------------------------------------------------ registry / sides / app
def test_registry_entry():
    st = S.STRATEGIES[KEY]
    assert st['name'] == '[TESTNET EXP] VWAP Rev L/S' and len(st['name']) <= 32
    assert S.EXP_TAG in st['desc'] and st['tf'] == '4h' and st['sides'] == 'both'
    g = S.merge_mgmt(KEY)
    assert g == dict(stop_atr=RV.STOP_ATR, max_bars=RV.CAP_BARS)            # 1.5 ATR stop, 30-candle cap, no R target
    assert st['params'] == dict(n=RV.N, k=RV.K, rg=RV.RG_ATR)


@pytest.mark.parametrize('sides', ['long', 'short'])
def test_side_masking(sides, monkeypatch):
    s_long, s_short = (sig(to_df(RS.build(n=90, side=sd, vwap=LE))) for sd in ('long', 'short'))
    assert s_long['le'].any() and s_short['se'].any()
    monkeypatch.setitem(S.STRATEGIES, KEY, dict(S.STRATEGIES[KEY], sides=sides))
    m_long = sig(to_df(RS.build(n=90, side='long', vwap=LE)), mask=True)
    m_short = sig(to_df(RS.build(n=90, side='short', vwap=LE)), mask=True)
    if sides == 'long':
        assert m_long['le'].any() and not m_short['se'].any()
    else:
        assert not m_long['le'].any() and m_short['se'].any()
    assert (m_long['lx'] == s_long['lx']).all()                            # exits are never masked


def test_validate_sleeve_and_meta_pick_up_4h():
    import app as A
    base = dict(share=0.25, risk=0.01, max_pos=4, symbols='core8')
    sl = A.validate_sleeve(dict(base, key=KEY), 0)
    assert sl['tf'] == '4h' and sl['sides'] == 'both' and sl['name'] == '[TESTNET EXP] VWAP Rev L/S'
    assert A.validate_sleeve(dict(base, key=KEY, sides='short'), 0)['sides'] == 'short'
    lib = A.App.meta(SimpleNamespace(engine=SimpleNamespace(rules=None)))['library']
    assert lib[KEY]['tf'] == '4h' and lib[KEY]['sides'] == 'both' and lib[KEY]['mgmt'] == dict(stop_atr=1.5, max_bars=30)


# ------------------------------------------------------------------ re-entry lock (Codex 6172827142, VWAPREV only)
T0 = pd.Timestamp('2026-11-16 00:00')                    # a 4h candle boundary (UTC)
H4 = pd.Timedelta(hours=4)


def _eng(history):
    return SimpleNamespace(history=list(history))


def _exit(side, at, sleeve='V', sym='BTCUSDT'):
    return dict(sleeve=sleeve, symbol=sym, side=side, closed=pd.Timestamp(at).tz_localize('UTC').isoformat())


def _lock(eng, side, dec_close, key=KEY, sleeve='V', sym='BTCUSDT'):
    """The engine's re-entry gate for a signal decided at the close `dec_close` (sg time = that candle's open)."""
    sl = E.sleeve(sleeve, key, 1.0, .01, 4, tf='4h')
    sg = dict(time=str(dec_close - H4), le=side == 'LONG', se=side == 'SHORT')
    return E.Engine.reentry_lock(eng, sl, sym, side, sg)


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_reentry_same_decision_bar_blocked(side):
    eng = _eng([_exit(side, T0 + pd.Timedelta(seconds=20))])     # signal exit decided at close T0, filled just after
    assert _lock(eng, side, T0)                                    # the same decision bar can never reopen
    assert _lock(eng, side, T0 + H4)                               # contract: trigger candle i-1 IS the exit candle


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_reentry_later_bar_allowed_and_stop_fill(side):
    eng = _eng([_exit(side, T0 + pd.Timedelta(seconds=20))])
    assert _lock(eng, side, T0 + 2 * H4) is None and _lock(eng, side, T0 + 10 * H4) is None
    stop = _eng([_exit(side, T0 + pd.Timedelta(hours=1))])        # intrabar stop in candle [T0, T0 + 4h)
    assert _lock(stop, side, T0 + H4) and _lock(stop, side, T0 + 2 * H4) is None


def test_reentry_opposite_side_not_locked():
    """Per-side books (range_vwap_rev.run_book): a long exit never locks the short book, and vice versa."""
    eng = _eng([_exit('LONG', T0 + pd.Timedelta(seconds=20))])
    assert _lock(eng, 'SHORT', T0) is None and _lock(eng, 'LONG', T0)
    eng = _eng([_exit('SHORT', T0 + pd.Timedelta(seconds=20))])
    assert _lock(eng, 'LONG', T0) is None and _lock(eng, 'SHORT', T0)


def test_reentry_lock_scoped_to_slot_and_coin():
    eng = _eng([_exit('LONG', T0 + pd.Timedelta(seconds=20))])
    assert _lock(eng, 'LONG', T0, sym='ETHUSDT') is None
    assert _lock(eng, 'LONG', T0, sleeve='W') is None


def test_reentry_guard_survives_within_a_run_and_restart():
    hist = [_exit('LONG', T0 + pd.Timedelta(seconds=20))]
    eng = _eng(hist)
    seq = [bool(_lock(eng, 'LONG', T0 + k * H4)) for k in range(4)]
    assert seq == [True, True, False, False]
    eng.history.append(_exit('LONG', T0 + 3 * H4 + pd.Timedelta(seconds=5)))   # a later trade exits again
    assert [bool(_lock(eng, 'LONG', T0 + k * H4)) for k in range(3, 7)] == [True, True, False, False]
    assert _lock(_eng(list(eng.history)), 'LONG', T0 + 4 * H4)                 # rebuilt from persisted history


@pytest.mark.parametrize('key', ['exp_range', 'exp_trend', 'exp_breakout'])
def test_other_families_unaffected(key):
    eng = _eng([_exit('LONG', T0 + pd.Timedelta(seconds=20)), _exit('SHORT', T0 + pd.Timedelta(seconds=20))])
    assert not S.STRATEGIES[key].get('reentry_lock')
    for side in ('LONG', 'SHORT'):
        assert _lock(eng, side, T0, key=key) is None                # can still reopen on the exit's decision bar


def test_reentry_lock_wired_into_entry_gate():
    import inspect
    assert S.STRATEGIES[KEY]['reentry_lock'] is True
    src = inspect.getsource(E.Engine.entry_block)
    assert 'self.reentry_lock(sl, sym, side, sg)' in src             # every automatic entry passes entry_block with sg
    assert 'self.entry_block(sl, sym, side, manual, sg=sg)' in inspect.getsource(E.Engine.open_lot)
