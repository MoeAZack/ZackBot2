"""Hostile tests for run_rsr1h (synthetic bars only; no market data, no economics)."""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_rsr1h as R

RULE = dict(step=0.001, min_qty=0.001, min_notional=5.0, tick=0.01)
H = pd.Timedelta(hours=1)
T0 = pd.Timestamp('2023-03-01 00:00')


def raw_panel(n_coins=12, n_h=420, seed=5):
    rng = np.random.default_rng(seed)
    t = pd.date_range(T0, periods=n_h, freq='h').values
    mkt = rng.normal(0, 0.006, n_h)
    raw = {}
    for k in range(n_coins):
        r = 0.0001 * k + (0.6 + 0.1 * k) * mkt + rng.normal(0, 0.004, n_h)
        c = 100 * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]]
        raw[f'C{k:02d}USDT'] = dict(t=t.copy(), o=o, h=np.maximum(o, c) * 1.001, l=np.minimum(o, c) * 0.999, c=c,
                                     elig=np.ones(n_h, bool), rule=RULE, atr=np.full(n_h, 0.5))
    return raw


def feats(raw):
    syms = sorted(raw)
    Rr, E = R.panel(raw, syms); M = R.loo_market(Rr, E)
    return Rr, E, M, R.features(Rr, M)


def test_loo_market_excludes_own_coin_and_counts_others():
    raw = raw_panel()
    Rr, E, M, _ = feats(raw)
    t = Rr.index[50]; s = 'C03USDT'
    others = [x for x in Rr.columns if x != s]
    assert abs(M.loc[t, s] - Rr.loc[t, others].mean()) < 1e-15
    raw2 = raw_panel(); raw2[s]['c'] = raw2[s]['c'] * np.exp(np.linspace(0, 1, 420))      # perturb own coin only
    _, _, M2, _ = feats(raw2)
    assert np.allclose(M[s].values, M2[s].values, equal_nan=True)
    assert not np.allclose(M['C04USDT'].values[1:], M2['C04USDT'].values[1:])


def test_contributor_threshold_8_others():
    raw = raw_panel(n_coins=10)
    for k in range(2, 10): raw[f'C{k:02d}USDT']['elig'][100] = False if k < 3 else True
    Rr, E, M, _ = feats(raw)                                         # at hour 100: 9 eligible -> 8 others for eligible coins
    t = Rr.index[100]
    assert np.isfinite(M.loc[t, 'C00USDT'])
    raw['C03USDT']['elig'][100] = False                              # 8 eligible -> 7 others
    Rr, E, M, _ = feats(raw)
    assert np.isnan(M.loc[t, 'C00USDT'])
    assert np.isfinite(M.loc[t, 'C02USDT'])                          # ineligible coin: 8 others still contribute


def _brute(Rr, M, s, t_idx):
    """Explicit prior-only OLS (with intercept) and population sigma of innovations using each hour's own fit."""
    def fit(j):
        x = M[s].values[j - 168:j]; y = Rr[s].values[j - 168:j]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 120 or np.var(x[ok]) <= 1e-18: return None
        b = np.cov(x[ok], y[ok], ddof=0)[0, 1] / np.var(x[ok]); a = y[ok].mean() - b * x[ok].mean()
        return a, b
    def innov(j):
        f = fit(j)
        if f is None or not (np.isfinite(Rr[s].values[j]) and np.isfinite(M[s].values[j])): return np.nan
        return Rr[s].values[j] - f[0] - f[1] * M[s].values[j]
    a, b = fit(t_idx)
    ev = np.array([innov(j) for j in range(t_idx - 168, t_idx)])
    ev = ev[np.isfinite(ev)]
    sig = ev.std() if len(ev) >= 120 else np.nan
    z = sum(innov(j) for j in (t_idx - 2, t_idx - 1, t_idx)) / (sig * np.sqrt(3))
    return a, b, sig, z


def test_features_match_brute_force_prior_only():
    raw = raw_panel()
    Rr, E, M, F = feats(raw)
    for s, j in (('C01USDT', 400), ('C07USDT', 360), ('C11USDT', 419)):
        a, b, sig, z = _brute(Rr, M, s, j)
        t = Rr.index[j]
        assert abs(F['alpha'].loc[t, s] - a) < 1e-10 and abs(F['beta'].loc[t, s] - b) < 1e-8
        assert abs(F['sigma'].loc[t, s] - sig) < 1e-10 and abs(F['z'].loc[t, s] - z) < 1e-6


def test_future_mutation_does_not_change_past_features():
    raw = raw_panel(); Rr, E, M, F = feats(raw)
    raw2 = raw_panel()
    for s in raw2:
        raw2[s]['c'][380:] *= 1.3; raw2[s]['elig'][390:] = False      # future prices and membership change
    raw2['C05USDT']['c'][380:] *= 0.5
    Rr2, E2, M2, F2 = feats(raw2)
    cut = Rr.index[379]
    for k in ('alpha', 'beta', 'e', 'sigma', 'z', 'm3'):
        assert np.allclose(F[k].loc[:cut].values, F2[k].loc[:cut].values, equal_nan=True, rtol=0, atol=1e-12), k
    assert np.allclose(M.loc[:cut].values, M2.loc[:cut].values, equal_nan=True)


def test_current_innovation_not_in_sigma():
    raw = raw_panel(); Rr, E, M, F = feats(raw)
    raw2 = raw_panel(); raw2['C02USDT']['c'][400:] *= 1.05            # jump AT hour 400 only changes e_400 and later
    _, _, _, F2 = feats(raw2)
    t = Rr.index[400]
    assert abs(F['sigma'].loc[t, 'C02USDT'] - F2['sigma'].loc[t, 'C02USDT']) < 1e-15
    assert abs(F['e'].loc[t, 'C02USDT'] - F2['e'].loc[t, 'C02USDT']) > 1e-3


def test_exact_affine_coin_has_near_zero_innovations_and_no_signal():
    raw = raw_panel()
    syms = sorted(raw); Rr, E = R.panel(raw, syms); M = R.loo_market(Rr, E)
    r = 0.0003 + 1.7 * M['C00USDT']                                   # coin exactly affine in its own LOO market
    Rr = Rr.copy(); Rr['C00USDT'] = r
    F = R.features(Rr, M)
    e = F['e']['C00USDT'].dropna()
    assert len(e) > 100 and np.abs(e).max() < 1e-12
    assert F['sigma']['C00USDT'].isna().all()                        # zero innovation variance -> no sigma
    assert (R.signals(F, E)['C00USDT'] == 0).all()


def test_insufficient_history_and_zero_market_variance():
    raw = raw_panel(); Rr, E, M, F = feats(raw)
    assert F['beta'].iloc[:121].isna().all().all()                   # returns start at hour 1: hour 120 has 119 prior pairs
    assert F['beta'].iloc[121:].notna().all().all()                  # hour 121 has exactly 120
    Mz = M.copy(); Mz.iloc[:300] = 0.001                              # constant market -> zero variance
    Fz = R.features(Rr, Mz)
    assert Fz['beta'].iloc[150:300].isna().all().all()


def test_missing_hour_breaks_returns_no_forward_fill():
    raw = raw_panel(); s = 'C04USDT'; a = raw[s]
    keep = np.r_[0:300, 301:420]
    for k in ('t', 'o', 'h', 'l', 'c', 'elig', 'atr'): a[k] = a[k][keep]
    Rr, E, M, F = feats(raw)
    t = Rr.index
    assert np.isnan(Rr.loc[t[300], s]) and np.isnan(Rr.loc[t[301], s]) and np.isfinite(Rr.loc[t[302], s])
    assert F['z'][s].loc[t[300]:t[303]].isna().all() and np.isfinite(F['z'][s].loc[t[304]])


def test_signal_levels_and_market_skip():
    idx = pd.date_range(T0, periods=4, freq='h')
    z = pd.DataFrame({'A': [-3.0, -2.999, 3.0, 3.5]}, index=idx)
    m3 = pd.DataFrame({'A': [0.0, 0.0, 0.0149, 0.015]}, index=idx)
    E = pd.DataFrame({'A': [True] * 4}, index=idx)
    sg = R.signals(dict(z=z, m3=m3), E)['A'].tolist()
    assert sg == [1, 0, -1, 0]
    m3n = m3.copy(); m3n.iloc[:, 0] = -0.015
    assert R.signals(dict(z=z, m3=m3n), E)['A'].tolist() == [0, 0, 0, 0]
    E2 = E.copy(); E2.iloc[0, 0] = False
    assert R.signals(dict(z=z, m3=m3), E2)['A'].tolist()[0] == 0


def test_prepare_maps_decision_to_next_open_and_atr_dist():
    raw = raw_panel(n_h=420)
    coins, F, sg = R.prepare(raw, sorted(raw))
    n = 0
    for s, c in coins.items():
        for d, sd, i, dist in c['dec']:
            n += 1
            assert d == c['a']['t'][i] + R.HOUR and sg.loc[pd.Timestamp(c['a']['t'][i]), s] == sd
            assert dist == 3.0 * c['a']['atr'][i]
    assert n == int((sg != 0).sum().sum())


def _lot(n=60):
    t = pd.date_range(T0, periods=n, freq='h').values
    return dict(t=t, o=np.full(n, 100.0), h=np.full(n, 100.2), l=np.full(n, 99.8), c=np.full(n, 100.0),
                elig=np.ones(n, bool), rule=RULE)


def test_time_exit_12h_from_actual_entry_both_paths():
    for mode in ('causal', 'worst'):
        t = R.run_lot(_lot(), 3, 1, 3.0, 7, mode)
        assert t['why'] == 'time' and t['t_in'] == pd.Timestamp(T0) + 4 * H
        assert t['held_h'] >= 12.0 and (mode == 'worst' or t['held_h'] == 12.0)


def test_target_2R_no_partial_and_identities():
    a = _lot(); e = 100 * (1 + R.SLIP); a['h'][7] = e + 2 * 3.0 + 0.01
    t = R.run_lot(a, 3, 1, 3.0, 15, 'causal')
    assert t['why'] == 'target' and t['part'] is None and t['q_final'] == t['qty']
    x = (e + 6.0) * (1 - R.SLIP); q = t['qty']
    assert abs(t['gross'] - (x - e) * q) < 1e-9
    assert abs(t['fees'] - (e * q + x * q) * R.FEES[15]) < 1e-12
    assert abs(t['fund'] - q * a['c'][4:8].sum() * R.FUND_H) < 1e-12
    assert abs(t['pnl'] - (t['gross'] - t['fees'] - t['fund'])) < 1e-9
    assert abs(q - np.floor(8 / 3.0 / 0.001 + 1e-9) * 0.001) < 1e-12


def test_stop_first_and_no_breakeven():
    a = _lot(); e = 100 * (1 + R.SLIP)
    a['h'][6] = e + 3.0 + 0.1                                         # +1R: nothing happens (no partial/BE)
    a['l'][8] = e - 0.5                                               # below entry: no BE exit
    a['h'][10] = e + 6.5; a['l'][10] = e - 3.1                        # stop and target same bar -> stop
    t = R.run_lot(a, 3, 1, 3.0, 7, 'causal')
    assert t['why'] == 'stop' and t['x_ref'] == e - 3.0 and t['t_out'] == pd.Timestamp(a['t'][10]) + H


def test_long_and_short_gap_exit_at_open():
    a = _lot(); e = 100 * (1 + R.SLIP); a['o'][6] = e - 4.0; a['l'][6] = e - 4.2
    t = R.run_lot(a, 3, 1, 3.0, 7, 'causal')
    assert t['why'] == 'stop_gap' and t['x_ref'] == a['o'][6] and t['t_out'] == pd.Timestamp(a['t'][6])
    a = _lot(); es = 100 * (1 - R.SLIP); a['o'][6] = es + 4.0; a['h'][6] = es + 4.2
    t = R.run_lot(a, 3, -1, 3.0, 7, 'causal')
    assert t['why'] == 'stop_gap' and t['x_ref'] == a['o'][6]
    assert abs(t['gross'] - (es - a['o'][6] * (1 + R.SLIP)) * t['qty']) < 1e-9
    a = _lot(); a['o'][4] = 90.0                                       # entry bar itself is never a gap exit
    t = R.run_lot(a, 3, 1, 3.0, 7, 'causal'); assert t['why'] != 'stop_gap'


def test_missing_next_hour_refused_and_terminal():
    a = _lot(); a['t'] = a['t'].copy(); a['t'][4:] = a['t'][4:] + np.timedelta64(1, 'h')
    assert R.run_lot(a, 3, 1, 3.0, 7, 'causal') == dict(refused='missing_next_hour')
    t = R.run_lot(_lot(10), 3, 1, 3.0, 7, 'causal')
    assert t['why'] == 'TERMINAL_OPEN' and t['t_out'] == pd.Timestamp(_lot(10)['t'][-1]) + H


def test_simulate_uses_rsr_lot_and_restores_harness():
    a = _lot(80)
    coins = {'AUSDT': dict(a=a, dec=[(a['t'][3] + R.HOUR, 1, 3, 3.0)])}
    tr, _ = R.simulate(coins, ['AUSDT'], ['AUSDT'], 7, 'causal')
    assert tr[0]['why'] == 'time' and tr[0]['held_h'] == 12.0 and R.X.run_lot is not R.run_lot


def test_frozen_constants():
    assert (R.WIN, R.MIN_OBS, R.MIN_CONTRIB, R.Z_TH, R.MKT_SKIP, R.STOP_ATR, R.TP_R, R.CAP_H) == (168, 120, 8, 3.0, 0.015, 3.0, 2.0, 12)
    assert len(R.ALL40) == 40 and len(set(R.ALL40)) == 40


if __name__ == '__main__':
    fs = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    for f in fs: f(); print('PASS', f.__name__)
