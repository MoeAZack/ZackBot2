"""Sprint families (EXPERIMENTAL / testnet-only): compact smoke fixtures, one block per family.

For every family: causal (future candles never change a decision), long/short mirror symmetry (a price reflection maps
every long decision onto the short one), no contradictory same-candle entry, an entry yields an order spec whose stop sits
on the correct side, the family's exits fire, and the LIVE engine (replay.run_replay: engine.Engine against a simulated
Binance) takes it LONG and SHORT through the shared sizing -> order -> exchange stop -> exit -> flatten path.
Synthetic, seeded data only - no profitability claim.
"""
import numpy as np, pandas as pd, pytest
import engine as E
import feasibility as F
import strategies as S
from replay import run_replay

SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
FREQ = {'4h': '4h', '1h': '1h', '15m': '15min'}
# key -> (timeframe, synthetic-shape kwargs, signal-fixture seeds, replay seeds per symbol); seeds are fixed, data is synthetic
FAMILIES = {'exp_trend': ('4h', dict(A=0.10, P=20.0), (10, 12, 2, 4), (10, 11, 12)),
            'exp_range': ('4h', dict(A=0.08, P=6.0, dr=0.002), (1, 2, 3, 4), (5, 6, 7)),
            'exp_breakout': ('4h', dict(A=0.10, P=20.0), (1, 2, 3, 4), (5, 6, 7)),
            'exp_scalp': ('15m', dict(A=0.04, P=10.0, dr=0.001), (1, 2, 3, 4), (5, 6, 7))}


def synth_one(n=700, seed=3, tf='4h', scale=100.0, A=0.06, P=12.0, seg=200, dr=0.004):
    """Regime-switching closes: up-trend, down-trend and flat segments, a slow swing of amplitude A / period P on top,
    heavy-tailed noise."""
    rng = np.random.default_rng(seed)
    drift = []
    for k in range(n // seg + 1):
        drift += [(dr, -dr, 0.0)[(k + seed) % 3]] * seg
    r = np.array(drift[:n]) + rng.standard_t(4, n) * 0.006
    c = scale * np.exp(np.cumsum(r) + A * np.sin(np.arange(n) / P))
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.004, n)) * c
    t = pd.date_range('2024-01-01', periods=n, freq=FREQ[tf])
    return pd.DataFrame(dict(t=t, o=o, h=np.maximum(o, c) + w, l=np.minimum(o, c) - w, c=c,
                             v=1000.0 + 500 * rng.random(n)))


def mirror(raw):
    """Price reflection about M: o,c -> M-o, M-c; h <-> M-l. Volume unchanged."""
    M = 2.0 * float(raw.h.max())
    return pd.DataFrame(dict(t=raw.t, o=M - raw.o, h=M - raw.l, l=M - raw.h, c=M - raw.c, v=raw.v))


def sig(key, raw):
    d = S.indicators(raw.reset_index(drop=True))
    ctx = dict(btc_down=np.zeros(len(d), bool), rank=pd.Series(999.0, index=d.index), rank7=pd.Series(999.0, index=d.index),
               n_symbols=1)
    return S.signals(key, d, ctx, None, mask_sides=False)


WARM = 220


@pytest.fixture(scope='module', params=sorted(FAMILIES))
def fam(request):
    key = request.param
    tf, kw, seeds, _ = FAMILIES[key]
    raws = [synth_one(seed=s, tf=tf, **kw) for s in seeds]
    return key, tf, raws, [sig(key, r) for r in raws]


# ------------------------------------------------------------------ registry contract
def test_registry_labels_and_both_sides():
    for key, (tf, _, _, _) in FAMILIES.items():
        st = S.STRATEGIES[key]
        assert 'EXPERIMENTAL' in st['name'] and 'testnet-only' in st['name']
        assert S.EXP_TAG in st['desc']
        assert st['sides'] == 'both' and st['tf'] == tf
        g = S.merge_mgmt(key)
        assert g.get('stop_atr', 0) > 0 and g.get('max_bars', 0) > 0      # protective stop + time exit always present
        assert 'dca' not in g and 'pyramid' not in g                       # no averaging / adds in v0


def test_entries_both_directions(fam):
    key, tf, raws, sigs = fam
    assert sum(int(s['le'][WARM:].sum()) for s in sigs) > 0, key
    assert sum(int(s['se'][WARM:].sum()) for s in sigs) > 0, key


def test_no_contradictory_same_candle(fam):
    key, tf, raws, sigs = fam
    for s in sigs:
        assert not (s['le'] & s['se']).any()
        assert not (s['le'] & s['lx']).any() and not (s['se'] & s['sx']).any()   # an entry never exits itself
        assert not (s['le'] & ~s['sx']).any() and not (s['se'] & ~s['lx']).any()  # opposite entry => held side exits first


def test_mirror_symmetry(fam):
    key, tf, raws, sigs = fam
    for raw, s in zip(raws, sigs):
        m = sig(key, mirror(raw))
        for a, b in (('le', 'se'), ('se', 'le'), ('lx', 'sx'), ('sx', 'lx')):
            diff = np.flatnonzero(s[a][WARM:] != m[b][WARM:])
            assert len(diff) == 0, (key, a, b, diff[:5] + WARM)


def test_causal_truncation(fam):
    key, tf, raws, sigs = fam
    raw, full = raws[0], sigs[0]
    hits = np.flatnonzero(full['le'] | full['se'])
    cuts = sorted(set([int(x) for x in hits[:6]] + [300, 450, 600, len(raw) - 2]))
    for k in cuts:
        cut = sig(key, raw.iloc[:k + 1])
        for col in ('le', 'se', 'lx', 'sx'):
            assert (cut[col] == full[col][:k + 1]).all(), (key, col, k)


def test_order_spec_stop_side(fam):
    key, tf, raws, sigs = fam
    g = S.merge_mgmt(key)
    d = S.indicators(raws[0])
    s = sigs[0]
    for col, sd in (('le', 1), ('se', -1)):
        i = int(np.flatnonzero(s[col])[0]) if s[col].any() else int(np.flatnonzero(sigs[1][col])[0])
        px, atr = float(d.c.iloc[i]), float(d.atr.iloc[i])
        z = F.risk_qty(g, 10.0, px, atr, sd)
        assert z['qty'] > 0 and z['R'] > 0
        assert (z['stop'] < px) if sd == 1 else (z['stop'] > px)
        assert abs(z['R'] - g['stop_atr'] * atr) < 1e-9 * px


# ------------------------------------------------------------------ family-specific exits
def _range_parts(raw):
    h, l, c, v = raw.h, raw.l, raw.c, raw.v
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    vw = (((h + l + c) / 3) * v).rolling(42).sum() / v.rolling(42).sum()
    return vw, atr


def test_range_exit_at_mean_and_on_invalidation():
    tf, kw, seeds, _ = FAMILIES['exp_range']
    raw = synth_one(seed=seeds[0], tf=tf, **kw)
    s = sig('exp_range', raw)
    vw, atr = _range_parts(raw)
    c = raw.c.values
    gate = ((vw - vw.shift(6)).abs() <= atr).values
    z = ((raw.c - vw) / atr).values
    for i in np.flatnonzero(s['le']):
        assert z[i - 1] <= -2 < z[i] and c[i] < vw[i] and gate[i]                     # the frozen entry rule, literally
    mean_hit = s['lx'] & (c >= vw.values)
    assert mean_hit.any() and (s['lx'] == ((c >= vw.values) | ~gate) & vw.notna().values & atr.notna().values).all()
    # invalidation: a flat range then a one-way slide - the long exit fires while price is still BELOW the mean
    n = 320
    t = pd.date_range('2024-01-01', periods=n, freq='4h')
    cc = 100 + 0.8 * np.sin(np.arange(n) / 3.0)
    cc[280:] = cc[279] - 1.5 * np.arange(1, n - 279)
    flat = pd.DataFrame(dict(t=t, o=np.r_[cc[0], cc[:-1]], h=cc + 0.3, l=cc - 0.3, c=cc, v=1000.0))
    flat['h'] = np.maximum(flat.h, flat.o + 0.3); flat['l'] = np.minimum(flat.l, flat.o - 0.3)
    fs = sig('exp_range', flat)
    fvw, fatr = _range_parts(flat)
    broke = np.flatnonzero(fs['lx'] & (flat.c.values < fvw.values))
    assert len(broke) and broke[0] > 280, broke[:3]
    assert S.merge_mgmt('exp_range') == dict(stop_atr=1.5, max_bars=30)


def test_breakout_rule_literal():
    tf, kw, seeds, _ = FAMILIES['exp_breakout']
    raw = synth_one(seed=seeds[0], tf=tf, **kw)
    s = sig('exp_breakout', raw)
    h, l, c = raw.h, raw.l, raw.c
    hh, ll = h.rolling(20).max().shift(), l.rolling(20).min().shift()
    for i in np.flatnonzero(s['le']):
        assert c[i] > hh[i] and not c[i - 1] > hh[i - 1]
    for i in np.flatnonzero(s['se']):
        assert c[i] < ll[i] and not c[i - 1] < ll[i - 1]
    assert (s['lx'] == (c < l.rolling(10).min().shift()).values).all()
    assert (s['sx'] == (c > h.rolling(10).max().shift()).values).all()


def test_scalp_rule_literal():
    tf, kw, seeds, _ = FAMILIES['exp_scalp']
    raw = synth_one(seed=seeds[0], tf=tf, **kw)
    s = sig('exp_scalp', raw)
    d = S.indicators(raw)
    for i in np.flatnonzero(s['le']):
        assert d.e20[i] > d.e50[i] and d.l[i] <= d.e20[i] < d.c[i] and d.c[i - 1] > d.e20[i - 1]
    for i in np.flatnonzero(s['se']):
        assert d.e20[i] < d.e50[i] and d.h[i] >= d.e20[i] > d.c[i] and d.c[i - 1] < d.e20[i - 1]
    assert (s['lx'] == (d.e20 < d.e50).values).all() and (s['sx'] == (d.e20 > d.e50).values).all()
    g = S.merge_mgmt('exp_scalp')
    assert g['stop_atr'] <= 1.0 and 1.0 <= g['tp_r'] <= 1.5 and g['max_bars'] <= 16


# ------------------------------------------------------------------ live engine path (simulated Binance)
def _sleeve(key, tf):
    return E.sleeve('X', key, 1.0, .02, 3, 'all', sides='both', tf=tf)


@pytest.fixture(scope='module')
def replayed():
    out = {}
    for key, (tf, kw, _, rseeds) in FAMILIES.items():
        raw = {s: synth_one(seed=rseeds[j], tf=tf, scale=100.0 * (j + 1), **kw) for j, s in enumerate(SYMS)}
        cap = []
        orig = E.Engine.connect

        def connect(self, *a, **k):
            cap.append(self)
            return orig(self, *a, **k)
        E.Engine.connect = connect
        try:
            r = run_replay(raw, [_sleeve(key, tf)], 260, steps=3, tf_sec=E.TF_SEC[tf])
        finally:
            E.Engine.connect = orig
        out[key] = (raw, r, cap[-1])
    return out


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_engine_takes_both_sides_with_exchange_stops(replayed, key):
    raw, r, eng = replayed[key]
    h = pd.DataFrame(r['history'])
    assert len(h), key
    assert set(h.side) == {'LONG', 'SHORT'}, (key, h.side.value_counts().to_dict())
    m = r['metrics']
    assert not r['mismatch'] and m['stops'] == m['lots'], (key, r['mismatch'], m)     # one exchange stop per open lot
    assert m['matched_pct'] >= 90, (key, m)                                              # backtester agrees trade by trade
    assert (h.risk_usd > 0).all()


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_engine_exits_fire(replayed, key):
    raw, r, eng = replayed[key]
    why = set(pd.DataFrame(r['history']).exit_reason)
    assert 'stop' in why or 'stop_loss' in why or any('stop' in w for w in why), (key, why)
    assert why & {'exit_signal', 'take_profit', 'time_exit'}, (key, why)


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_engine_emergency_flatten(replayed, key):
    """Stop the replay while a lot of this family is open, then flatten: every lot closes and its exchange stop goes."""
    raw, r, _ = replayed[key]
    tf = FAMILIES[key][0]
    h = pd.DataFrame(r['history'])
    long_ = h[h.hours >= 2 * E.TF_SEC[tf] / 3600].iloc[0]
    t_index = {ts: k for k, ts in enumerate(raw[SYMS[0]].t)}
    j = t_index[pd.Timestamp(long_.opened).tz_localize(None).floor(f'{E.TF_SEC[tf]}s')] + 1
    cap = []
    orig = E.Engine.connect

    def connect(self, *a, **k):
        cap.append(self)
        return orig(self, *a, **k)
    E.Engine.connect = connect
    try:
        snap = run_replay(raw, [_sleeve(key, tf)], 260, steps=3, tf_sec=E.TF_SEC[tf], stop_at=(j, 0, 1))
    finally:
        E.Engine.connect = orig
    eng = cap[-1]
    assert snap['lots'] and eng.state['lots'], key
    res = eng.flatten()
    assert not res['failed'] and not eng.state['lots'] and res['closed']
    assert eng.trade.positions() == {} and all(not eng.trade.open_stop_tags(s) for s in SYMS)
    assert eng.S['ENTRIES_PAUSED'] is True
    assert eng.history[-1]['exit_reason'] == 'flatten'


@pytest.mark.parametrize('key', sorted(FAMILIES))
def test_engine_time_exit(key):
    """The family's time cap is the engine's max_bars exit: shortened to 1 candle on a short replay, it must close lots."""
    tf, kw, _, rseeds = FAMILIES[key]
    raw = {s: synth_one(n=420, seed=rseeds[j], tf=tf, scale=100.0 * (j + 1), **kw) for j, s in enumerate(SYMS)}
    sl = E.sleeve('X', key, 1.0, .02, 3, 'all', sides='both', tf=tf, mgmt=dict(max_bars=1, tp_r=0))
    sl['mgmt'] = S.merge_mgmt(key, sl['mgmt'])
    r = run_replay(raw, [sl], 230, steps=3, tf_sec=E.TF_SEC[tf])
    why = set(pd.DataFrame(r['history']).exit_reason) if r['history'] else set()
    assert 'time_exit' in why, (key, why)
    assert not r['mismatch'] and r['metrics']['stops'] == r['metrics']['lots']


def test_slot_timeframe_defaults_to_the_registered_strategy_timeframe():
    """Codex 6162610022: a client that omits tf must not run exp_scalp (15m-tuned exits) on 4h. Explicit tf still wins;
    the strategy library exposes the registered timeframe."""
    import app as A
    base = dict(share=0.25, risk=0.01, max_pos=2, symbols='core8', sides='both')
    assert A.validate_sleeve(dict(base, key='exp_scalp'), 0)['tf'] == '15m'
    assert A.validate_sleeve(dict(base, key='exp_trend'), 0)['tf'] == '4h'
    assert A.validate_sleeve(dict(base, key='exp_scalp', tf='1h'), 0)['tf'] == '1h'
    from types import SimpleNamespace
    lib = A.App.meta(SimpleNamespace(engine=SimpleNamespace(rules=None)))['library']
    assert lib['exp_scalp']['tf'] == '15m' and lib['exp_trend']['tf'] == '4h'
