"""VWAPREV quote volume (qv, Binance kline field 7) through the generic replay / backtest / lab candle sources.

Synthetic only: no network, no market-data store, no candle cache outside the test temp folder (conftest points
LOCALAPPDATA at a throw-away root). Covers: qv survives app.get_candles (the shared backtest / lab cache), backtest.Book,
replay.run_replay's simulated Binance feed and lab._raw_from; a pre-qv cache still loads (qv NaN) and a source without qv
gives no VWAPREV trade (never a synthesized volume); VWAPREV signals on the replay path equal the live path (engine.candles
on raw Binance kline rows); every other family is unchanged by the presence of qv."""
import os
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backtest as B
import engine as E
import lab as L
import strategies as S
from replay import run_replay

SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
KEY = 'exp_vwaprev'
H4 = 14_400_000
N, T0 = 330, 260


def tape(seed, n=N, scale=100.0, freq='4h'):
    """Fixed-seed OHLCV with a real-looking quote volume (typical price x volume, small noise)."""
    rng = np.random.default_rng(seed)
    r = rng.standard_t(4, n) * 0.004
    c = scale * np.exp(np.cumsum(r) + 0.05 * np.sin(np.arange(n) / 5.0))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.004, n)) * c
    h, l = np.maximum(o, c) + w, np.minimum(o, c) - w
    v = np.round(1000.0 + 500 * rng.random(n), 3)
    qv = np.round((h + l + c) / 3 * v * (1 + rng.normal(0, 0.001, n)), 6)
    t = pd.date_range('2024-01-01', periods=n, freq=freq)
    return pd.DataFrame(dict(t=t, o=o, h=h, l=l, c=c, v=v, qv=qv))


def book_raw(with_qv=True, freq='4h'):
    raw = {s: tape(i + 1, scale=100.0 * (i + 1), freq=freq) for i, s in enumerate(SYMS)}
    return raw if with_qv else {s: d.drop(columns='qv') for s, d in raw.items()}


def binance_rows(d, tf_ms=H4):
    """The frame as raw Binance kline rows (12 fields, strings like the API)."""
    ms = d.t.astype('datetime64[ns]').astype('int64') // 1_000_000
    return [[int(t), repr(o), repr(h), repr(l), repr(c), repr(v), int(t) + tf_ms - 1, repr(q), 0, '0', '0', '0']
            for t, o, h, l, c, v, q in zip(ms, d.o, d.h, d.l, d.c, d.v, d.qv)]


def live_frame(d):
    """The LIVE engine path: engine.Engine.candles on raw Binance kline rows."""
    rows = binance_rows(d)
    fake = SimpleNamespace(_kc={}, data=SimpleNamespace(klines=lambda *a, **k: rows))
    return E.Engine.candles(fake, 'BTCUSDT', '4h')


def ctx1(d):
    return dict(btc_down=np.zeros(len(d), bool), rank=pd.Series(999.0, index=d.index),
                rank7=pd.Series(999.0, index=d.index), n_symbols=1)


def vsig(d):
    return S.signals(KEY, d, ctx1(d), None, mask_sides=False)


def sleeve(key, tf='4h'):
    return E.sleeve('X', key, 1.0, .02, 3, 'all', sides='both', tf=tf)


def bt_run(raw, key, tf='4h'):
    bk = B.Book(raw)
    sl = sleeve(key, tf)
    cfg = [dict(key=key, share=sl['share'], risk=sl['risk'], max_pos=sl['max_pos'], symbols=list(raw), sides=sl['sides'],
                mgmt=sl['mgmt'])]
    return B.run(bk, cfg, start=500.0, max_lev=10, t0=bk.t[T0], fund_per_bar=B.FUND_PER_BAR * E.TF_SEC[tf] / 14400)


def replay(raw, key=KEY):
    cap, orig = [], E.Engine.connect

    def connect(self, *a, **k):
        cap.append(self)
        return orig(self, *a, **k)
    E.Engine.connect = connect
    try:
        r = run_replay(raw, [sleeve(key)], T0, steps=3)
    finally:
        E.Engine.connect = orig
    return r, cap[-1]


@pytest.fixture(scope='module')
def rep_qv():
    return replay(book_raw(True))


# ------------------------------------------------------------------ app.get_candles (backtest / lab cache)
class _Pub:
    """A public-klines stand-in over a fixed 12-field Binance row list (no network)."""
    def __init__(self, rows, short=False):
        self.rows = [r[:7] if short else r for r in rows]

    def klines(self, sym, tf, limit=1500, end_time=None, start_time=None):
        r = self.rows
        if end_time is not None: r = [x for x in r if x[0] <= end_time][-limit:]
        if start_time is not None: r = [x for x in r if x[0] >= start_time][:limit]
        return r


def _cache_env(monkeypatch, tmp_path, n=400):
    import app as A
    os.makedirs(tmp_path / 'candles', exist_ok=True)
    monkeypatch.setattr(A, 'DATA', str(tmp_path))
    last = (int(pd.Timestamp.now('UTC').timestamp() * 1000) // H4 - 2) * H4         # last fully closed 4h candle
    d = tape(7, n=n)
    d['t'] = pd.to_datetime([last - (n - 1 - i) * H4 for i in range(n)], unit='ms')
    return A, d, binance_rows(d), tmp_path / 'candles' / 'BTCUSDT_4h.csv'


def test_get_candles_keeps_real_qv(monkeypatch, tmp_path):
    A, d, rows, f = _cache_env(monkeypatch, tmp_path)
    monkeypatch.setattr(A, 'PUB', _Pub(rows))
    out = A.get_candles('BTCUSDT', '4h', 30)
    assert list(out.columns) == ['t', 'o', 'h', 'l', 'c', 'v', 'qv']
    m = d.set_index('t').qv
    assert np.array_equal(out.qv.to_numpy(), m.loc[out.t].to_numpy())             # real field 7, never close * volume
    assert 'qv' in pd.read_csv(f).columns
    again = A.get_candles('BTCUSDT', '4h', 30)                                     # reload from the cache file
    assert np.array_equal(again.qv.to_numpy(), out.qv.to_numpy())


def test_get_candles_old_cache_loads_with_nan_qv(monkeypatch, tmp_path):
    A, d, rows, f = _cache_env(monkeypatch, tmp_path)
    old = d.iloc[:300].copy()
    old['t'] = old.t.astype('datetime64[ns]').astype('int64') // 1_000_000
    old[['t', 'o', 'h', 'l', 'c', 'v']].to_csv(f, index=False)                    # pre-qv cache format
    monkeypatch.setattr(A, 'PUB', _Pub(rows))
    out = A.get_candles('BTCUSDT', '4h', 30)
    assert len(out) == len(d) and out.qv.iloc[:300].isna().all()                  # old rows: no synthesized volume
    assert np.array_equal(out.qv.iloc[300:].to_numpy(), d.qv.iloc[300:].to_numpy())  # newly fetched rows carry it
    assert np.allclose(out.c.to_numpy(), d.c.to_numpy(), rtol=1e-12, atol=0)                  # CSV round trip


def test_get_candles_source_without_qv_is_nan(monkeypatch, tmp_path):
    A, d, rows, f = _cache_env(monkeypatch, tmp_path)
    monkeypatch.setattr(A, 'PUB', _Pub(rows, short=True))
    out = A.get_candles('BTCUSDT', '4h', 30)
    assert out.qv.isna().all()
    s = vsig(S.indicators(out))
    assert not (s['le'].any() or s['se'].any())


# ------------------------------------------------------------------ backtest.Book / lab
def test_book_and_lab_carry_qv():
    raw = book_raw(True)
    bk = B.Book(raw)
    for s in SYMS:
        assert np.array_equal(bk.d[s].qv.to_numpy(), raw[s].qv.to_numpy())
    back = L._raw_from(bk)
    assert all(np.array_equal(back[s].qv.to_numpy(), raw[s].qv.to_numpy()) for s in SYMS)
    assert 'qv' not in L._raw_from(B.Book(book_raw(False)))['BTCUSDT']
    r = L.lookahead_check(bk, keys=[KEY], n_points=4, min_bar=200, check_regime=False)
    assert r['ok'] and r['strategies'][KEY]['checked'] > 0


def test_book_vwaprev_signals_equal_live_path():
    raw = book_raw(True)
    bk = B.Book(raw)
    n_entries = 0
    for s in SYMS:
        bs, ls = bk.raw_sig(KEY, s), vsig(live_frame(raw[s]))
        for col in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(np.asarray(bs[col], bool), np.asarray(ls[col], bool)), (s, col)
        assert np.array_equal(bs['atr'], ls['atr'], equal_nan=True)
        n_entries += int(bs['le'].sum() + bs['se'].sum())
    assert n_entries > 0


def test_backtest_without_qv_has_no_vwaprev_trades():
    tr, _ = bt_run(book_raw(True), KEY)
    assert len(tr) > 0
    for raw in (book_raw(False), {s: d.assign(qv=np.nan) for s, d in book_raw(True).items()}):
        tr, cv = bt_run(raw, KEY)
        assert len(tr) == 0 and float(cv.iloc[-1]) == pytest.approx(500.0, rel=0.02)


# ------------------------------------------------------------------ replay (live engine against the simulated Binance)
def test_replay_feed_carries_qv(rep_qv):
    r, eng = rep_qv
    raw = book_raw(True)
    for s in SYMS:
        df = eng._kc[(s, '4h')][0]
        assert len(df) and not df.qv.isna().any()
        want = raw[s].set_index('t').qv.loc[pd.to_datetime(df.t)].to_numpy()
        assert np.array_equal(df.qv.to_numpy(), want)


def test_replay_vwaprev_signals_match_live_path(rep_qv):
    r, eng = rep_qv
    raw = book_raw(True)
    live = {s: vsig(live_frame(raw[s])) for s in SYMS}
    for s in SYMS:                                                   # the replay engine's own last candle frame
        df = eng._kc[(s, '4h')][0]
        rs = vsig(df)
        n = len(df)
        for col in ('le', 'se', 'lx', 'sx'):
            assert np.array_equal(np.asarray(rs[col], bool), np.asarray(live[s][col], bool)[:n]), (s, col)
    eh = r['engine_trades']
    assert len(eh) > 0
    for _, x in eh.iterrows():                                       # every replay entry sits on a live-path signal bar
        col = 'le' if x.side == 1 else 'se'
        assert live[x.sym][col][int(x.i_in) - 1], (x.sym, x.side, x.i_in)
    # engine vs backtester on the same candles: every interior entry matches; only window edges may differ (the engine's
    # first cycle at T0 vs the backtest's T0 + 1 start, and positions the engine still holds when the tape ends)
    mt = r['matched']
    inner = mt[(mt.i_in > T0 + 1) & (mt.i_in < N - 5)]
    assert len(inner) >= 4 and (inner._merge == 'both').all()
    assert r['metrics']['mismatches'] == 0


def test_replay_without_qv_has_no_vwaprev_trades():
    r, eng = replay(book_raw(False))
    assert eng._kc[('BTCUSDT', '4h')][0].qv.isna().all()
    assert r['metrics']['trades_engine'] == 0 and r['metrics']['trades_bt'] == 0


# ------------------------------------------------------------------ other families: byte-identical with or without qv
OTHER = sorted(k for k in S.STRATEGIES if k != KEY)


@pytest.mark.parametrize('key', OTHER)
def test_other_families_backtest_unchanged_by_qv(key):
    tf = S.STRATEGIES[key].get('tf', '4h')
    freq = {'4h': '4h', '1h': '1h', '15m': '15min'}[tf]
    a_tr, a_cv = bt_run(book_raw(True, freq), key, tf)
    b_tr, b_cv = bt_run(book_raw(False, freq), key, tf)
    pd.testing.assert_frame_equal(a_tr, b_tr, check_exact=True)
    pd.testing.assert_series_equal(a_cv, b_cv, check_exact=True)


def test_other_family_replay_unchanged_by_qv():
    a, _ = replay(book_raw(True), 'exp_range')
    b, _ = replay(book_raw(False), 'exp_range')
    assert a['metrics'] == b['metrics'] and a['history'] == b['history']
    pd.testing.assert_series_equal(a['engine_curve'], b['engine_curve'], check_exact=True)
