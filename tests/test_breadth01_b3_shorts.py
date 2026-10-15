"""BREADTH-01 B3 (Codex ruling 6186050896 pt 4): two short-only EXPERIMENTAL / testnet-only mirrors.

exp_mom_short    = hot_coin mirrored: bottom-3 7-day mover ("cold") breaking below the prior 20-candle low and EMA50 on a
                   volume surge; exit on a close back above EMA20; hot_coin's mgmt (stop 2 ATR, half at 2R, 2.5 ATR trail).
exp_rip_sell_dca = dca_dip mirrored: in a downtrend sell the first close > 1.5 ATR above EMA20; DCA basket with safety
                   orders ABOVE, basket TP below, mandatory hard basket stop (stop_atr) above the last order.

Mirror property: a price reflection (o,h,l,c -> M - x, h <-> l, volume unchanged) maps EMA / ATR / channels exactly, so
dca_dip long entries map one-to-one onto exp_rip_sell_dca short entries, and hot_coin's price+volume part onto
exp_mom_short's. The one documented asymmetry: hot / cold come from the cross-sectional rank of the 42-candle return
(build_context rank7). A reflection does not negate returns (ret(M - c) != -ret(c)), so the rank itself is not mirrored;
the mirror test therefore pins the context (hot rank 1 vs cold rank n) and a separate test checks the cold rule on a real
build_context. Synthetic, seeded data only - no profitability claim, nothing enabled.
"""
import numpy as np, pandas as pd, pytest
import backtest as B
import feasibility as F
import strategies as S

KEYS = ('exp_mom_short', 'exp_rip_sell_dca')
WARM = 220
NB = 10                                   # pinned universe size for the hot/cold context


def _frame(c, v, seed, tf='4h'):
    rng = np.random.default_rng(seed + 1000)
    n = len(c)
    o = np.r_[c[0], c[:-1]]
    w = np.abs(rng.normal(0, 0.003, n)) * c
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq=tf), o=o, h=np.maximum(o, c) + w,
                             l=np.minimum(o, c) - w, c=c, v=v))


def pumps(n=900, seed=1, every=60, jump=0.04):
    """Choppy closes with a 3-candle volume-surge pump every `every` candles (hot_coin breakouts); mirrored -> dumps."""
    rng = np.random.default_rng(seed)
    r = rng.standard_t(4, n) * 0.005
    v = 1000.0 + 300 * rng.random(n)
    for k in range(every, n - 3, every):
        r[k:k + 3] += jump; v[k:k + 3] *= 4
        r[k + 3:k + 15] -= jump * 3 / 12          # give most of it back so the trend stays flat
    return _frame(100.0 * np.exp(np.cumsum(r)), v, seed)


def dips(n=900, seed=1, every=50, drop=0.035, dr=0.003):
    """Up-trend with sharp 2-candle dips every `every` candles (dca_dip entries); mirrored -> down-trend with rips."""
    rng = np.random.default_rng(seed)
    r = dr + rng.standard_t(4, n) * 0.004
    for k in range(every, n - 2, every):
        r[k:k + 2] -= drop
    return _frame(100.0 * np.exp(np.cumsum(r)), 1000.0 + 300 * rng.random(n), seed)


def mirror(raw, M=None):
    M = 2.0 * float(raw.h.max()) if M is None else M
    return pd.DataFrame(dict(t=raw.t, o=M - raw.o, h=M - raw.l, l=M - raw.h, c=M - raw.c, v=raw.v))


def ctx(n, rank7):
    idx = pd.RangeIndex(n)
    return dict(btc_down=np.zeros(n, bool), rank=pd.Series(999.0, index=idx), rank7=pd.Series(float(rank7), index=idx),
                n_symbols=NB)


def sig(key, raw, rank7=NB, mask=False):
    d = S.indicators(raw.reset_index(drop=True))
    return S.signals(key, d, ctx(len(d), rank7), None, mask_sides=mask)


FIX = {'exp_mom_short': lambda s: mirror(pumps(seed=s)), 'exp_rip_sell_dca': lambda s: mirror(dips(seed=s))}
SEEDS = (1, 2, 3)


# ------------------------------------------------------------------ registry contract
@pytest.mark.parametrize('key', KEYS)
def test_registry_conventions(key):
    st = S.STRATEGIES[key]
    assert st['name'].startswith('[TESTNET EXP] ') and S.EXP_TAG in st['desc']
    assert st['sides'] == 'short' and st['tf'] == '4h'
    g = S.merge_mgmt(key)
    assert g.get('stop_atr', 0) > 0 or g.get('dca', {}).get('stop_atr', 0) > 0      # mandatory stop
    assert S.STRATEGIES['exp_mom_short']['name'] == '[TESTNET EXP] Mom Short (dump)'
    assert S.STRATEGIES['exp_rip_sell_dca']['name'] == '[TESTNET EXP] Rip-sell DCA S'


def test_mgmt_is_exactly_the_mirrored_strategies_mgmt():
    assert S.STRATEGIES['exp_mom_short']['mgmt'] == S.STRATEGIES['hot_coin']['mgmt']
    assert S.STRATEGIES['exp_rip_sell_dca']['mgmt'] == S.STRATEGIES['dca_dip']['mgmt']
    assert not S.STRATEGIES['exp_mom_short'].get('params') and not S.STRATEGIES['exp_rip_sell_dca'].get('params')


def test_rip_sell_dca_labelled_unverified_like_dca_dip():
    """dca_dip's UNVERIFIED label lives on the presets that hold it (engine.PRESETS); no preset holds the rip-sell DCA, so
    the label sits in its own description."""
    assert 'UNVERIFIED' in S.STRATEGIES['exp_rip_sell_dca']['desc']
    assert 'UNVERIFIED' not in S.STRATEGIES['exp_mom_short']['desc']


def test_validate_sleeve_contract_holds_for_new_keys():
    """app.validate_sleeve (read, not edited): key must be in STRATEGIES, sides default to the strategy's, tf to the
    strategy's tf, and a DCA slot needs dca.stop_atr in the slot OR the strategy default - exp_rip_sell_dca has it."""
    for k in KEYS:
        assert k in S.STRATEGIES and S.STRATEGIES[k]['sides'] in ('long', 'short', 'both')
        assert S.STRATEGIES[k]['tf'] in ('15m', '1h', '4h')
    assert S.STRATEGIES['exp_rip_sell_dca']['mgmt']['dca']['stop_atr'] > 0


# ------------------------------------------------------------------ side discipline
@pytest.mark.parametrize('key', KEYS)
def test_fires_short_only(key):
    tot = 0
    for s in SEEDS:
        g = sig(key, FIX[key](s))
        assert not g['le'].any() and not g['lx'].any(), key           # never long, even unmasked
        assert not (g['se'] & g['sx']).any()
        tot += int(g['se'][WARM:].sum())
    assert tot >= 3, (key, tot)


def test_mom_short_needs_cold_volume_and_breakdown():
    raw = FIX['exp_mom_short'](1)
    d = S.indicators(raw)
    g = sig('exp_mom_short', raw)
    i = np.flatnonzero(g['se'])
    assert len(i) and (d.vol_ratio.values[i] > 1.5).all() and (d.c.values[i] < d.ll20.values[i]).all() \
        and (d.c.values[i] < d.e50.values[i]).all()
    assert not sig('exp_mom_short', raw, rank7=1)['se'].any()             # a hot coin never shorts here
    assert not sig('exp_mom_short', raw, rank7=999)['se'].any()           # unranked (fill 999) is never cold
    flat = raw.assign(v=1000.0)                                           # no volume surge -> no entry
    assert not sig('exp_mom_short', flat)['se'].any()


def test_rip_sell_needs_downtrend():
    up = dips(seed=1)                                                     # the up-trend itself: e50 > e200, never sells
    assert not sig('exp_rip_sell_dca', up)['se'][WARM:].any()


# ------------------------------------------------------------------ mirror property
@pytest.mark.parametrize('seed', SEEDS)
def test_mirror_hot_coin_to_mom_short(seed):
    raw = pumps(seed=seed)
    h = sig('hot_coin', raw, rank7=1)                                    # pinned hot (top-3)
    m = sig('exp_mom_short', mirror(raw), rank7=NB)                      # pinned cold (bottom-3)
    assert h['le'][WARM:].sum() > 0
    assert (h['le'][WARM:] == m['se'][WARM:]).all()
    assert (h['lx'][WARM:] == m['sx'][WARM:]).all()


@pytest.mark.parametrize('seed', SEEDS)
def test_mirror_dca_dip_to_rip_sell(seed):
    raw = dips(seed=seed)
    a, b = sig('dca_dip', raw), sig('exp_rip_sell_dca', mirror(raw))
    assert a['le'][WARM:].sum() > 0
    for x, y in (('le', 'se'), ('lx', 'sx'), ('se', 'le'), ('sx', 'lx')):
        assert (a[x][WARM:] == b[y][WARM:]).all(), (x, y)


def test_cold_rule_on_real_context():
    """Closest property to the rank mirror: with a real build_context, cold = the 3 weakest 42-candle returns of the
    universe (rank7 in n-2..n) and the strongest coin is never cold."""
    n = 700
    raws = {f'C{k}USDT': _frame(100 * np.exp(np.linspace(0, 0.5 - 0.2 * k, n)), np.full(n, 1000.0), k) for k in range(5)}
    raws['BTCUSDT'] = raws.pop('C0USDT')
    bk = B.Book(raws)
    for s, c in bk.ctx.items():
        r = c['rank7'].values[WARM:]
        cold = (r > c['n_symbols'] - 3) & (r <= c['n_symbols'])
        assert cold.all() == (s in ('C2USDT', 'C3USDT', 'C4USDT')) and (cold.any() == cold.all())


# ------------------------------------------------------------------ causality + restart
@pytest.mark.parametrize('key', KEYS)
def test_prefix_causality(key):
    raw = FIX[key](2)
    full = sig(key, raw)
    hits = np.flatnonzero(full['se'] | full['sx'])
    for k in sorted(set([int(x) for x in hits[:6]] + [300, 500, 700, len(raw) - 2])):
        cut = sig(key, raw.iloc[:k + 1])
        for col in ('le', 'se', 'lx', 'sx'):
            assert (cut[col] == full[col][:k + 1]).all(), (key, col, k)


@pytest.mark.parametrize('key', KEYS)
def test_restart_recompute(key):
    """A restarted engine refetches a 1500-candle window and recomputes from scratch: no hidden state, the input frame is
    not mutated, and the decisions over the recent tail match the full-history ones."""
    raw = pd.concat([FIX[key](s) for s in (1, 2)], ignore_index=True)   # 1800 candles
    raw['t'] = pd.date_range('2024-01-01', periods=len(raw), freq='4h')
    raw['c'] = raw.c.where(raw.index < 900, raw.c * raw.c.iloc[899] / raw.c.iloc[900])
    raw['o'], raw['h'], raw['l'] = np.r_[raw.c[0], raw.c[:-1]], raw[['o', 'c']].max(axis=1) * 1.002, raw[['o', 'c']].min(axis=1) * 0.998
    raw['h'], raw['l'] = np.maximum(raw.h, np.maximum(raw.o, raw.c)), np.minimum(raw.l, np.minimum(raw.o, raw.c))
    before = raw.copy()
    full = sig(key, raw)
    assert raw.equals(before)
    again = sig(key, raw.copy())
    win = sig(key, raw.iloc[-1500:])
    for col in ('le', 'se', 'lx', 'sx'):
        assert (again[col] == full[col]).all()
        assert (win[col][-200:] == full[col][-200:]).all(), (key, col)


# ------------------------------------------------------------------ stops + martingale_risk
@pytest.mark.parametrize('key', KEYS)
def test_stop_above_entry(key):
    g = S.merge_mgmt(key)
    raw = FIX[key](1)
    d = S.indicators(raw)
    i = int(np.flatnonzero(sig(key, raw)['se'])[0])
    px, atr = float(d.c.iloc[i]), float(d.atr.iloc[i])
    z = F.risk_qty(g, 10.0, px, atr, -1)
    assert z['qty'] > 0 and z['stop'] > px
    if 'dca' in g:
        assert all(b > a for a, b in zip(z['levels'], z['levels'][1:]))     # safety orders ABOVE the entry
        assert z['stop'] > z['levels'][-1]
        assert abs(z['stop'] - (z['levels'][-1] + g['dca']['stop_atr'] * atr)) < 1e-9 * px
    else:
        assert abs(z['stop'] - (px + g['stop_atr'] * atr)) < 1e-9 * px


def test_rip_sell_has_hard_stop_and_martingale_risk_accepts_it():
    g = S.merge_mgmt('exp_rip_sell_dca')
    r = B.martingale_risk(g, 0.02)
    assert r == B.martingale_risk(S.merge_mgmt('dca_dip'), 0.02)               # inside the same caps as dca_dip
    assert 1.0 <= r['worst_loss_r'] < 1.1 and r['depth_pct'] > 0
    with pytest.raises(ValueError):
        B.martingale_risk(S.merge_mgmt('exp_rip_sell_dca', dict(dca=dict(stop_atr=0))), 0.02)


# ------------------------------------------------------------------ backtest machinery (import only)
def _book_with(key, raw, se_at=None, sx_at=None):
    bk = B.Book({'BTCUSDT': raw})
    if se_at is not None:
        n = len(raw); z = np.zeros(n, bool)
        se, sx = z.copy(), z.copy(); se[se_at] = True
        if sx_at is not None: sx[sx_at] = True
        bk._sig[(key, 'BTCUSDT', 'None')] = dict(le=z.copy(), se=se, lx=z.copy(), sx=sx)
    return bk


def _run(bk, key, start=500.0, **kw):
    sl = [dict(key=key, share=1.0, risk=0.02, max_pos=1, mgmt={}, symbols=['BTCUSDT'])]   # sides from the registry
    tr, cv = B.run(bk, sl, start=start, warmup=WARM, maint_margin=0, fund_per_bar=0, **kw)
    return tr


@pytest.mark.parametrize('key', KEYS)
def test_backtest_takes_real_signals_short_only_and_exits(key):
    tr = pd.concat([_run(B.Book({'BTCUSDT': FIX[key](s)}), key).assign(seed=s) for s in SEEDS], ignore_index=True)
    assert len(tr) >= 3 and (tr.side == -1).all() and (tr.i_out >= tr.i_in).all()
    assert set(tr.why) - {'end'}, tr.why.tolist()


def _scripted(path):
    """Flat 100 for 300 candles (ATR ~ 1 from +-0.5 wicks), then the given closes."""
    c = np.r_[np.full(300, 100.0), np.asarray(path, float), np.full(40, path[-1])]
    n = len(c)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame(dict(t=pd.date_range('2024-01-01', periods=n, freq='4h'), o=o, h=np.maximum(o, c) + 0.5,
                             l=np.minimum(o, c) - 0.5, c=c, v=1000.0))


def test_backtest_rip_sell_dca_basket_hits_hard_stop_above():
    raw = _scripted(np.linspace(100, 130, 30))                            # straight rally through every safety order
    tr = _run(_book_with('exp_rip_sell_dca', raw, se_at=300), 'exp_rip_sell_dca', start=5000.0)   # BTC min notional 50
    assert len(tr) == 1 and tr.side.iloc[0] == -1 and tr.why.iloc[0] == 'stop'
    assert -1.2 < tr.R.iloc[0] < -0.9                                      # the full basket loses ~1R, never more


def test_backtest_rip_sell_dca_basket_tp_below():
    raw = _scripted(np.r_[np.linspace(100, 102.5, 4), np.linspace(102.5, 90, 10)])   # one safety order up, then a drop
    tr = _run(_book_with('exp_rip_sell_dca', raw, se_at=300), 'exp_rip_sell_dca', start=5000.0)   # BTC min notional 50
    assert len(tr) == 1 and tr.side.iloc[0] == -1 and tr.why.iloc[0] == 'tp' and tr.R.iloc[0] > 0


def test_backtest_mom_short_exit_signal_and_stop():
    raw = _scripted(np.r_[np.linspace(100, 97, 4), np.full(5, 97.0)])
    tr = _run(_book_with('exp_mom_short', raw, se_at=300, sx_at=305), 'exp_mom_short')
    assert len(tr) == 1 and tr.side.iloc[0] == -1 and tr.i_out.iloc[0] >= 305
    raw = _scripted(np.linspace(100, 120, 20))
    tr = _run(_book_with('exp_mom_short', raw, se_at=300), 'exp_mom_short')
    assert len(tr) == 1 and tr.why.iloc[0] == 'stop' and tr.R.iloc[0] < 0
