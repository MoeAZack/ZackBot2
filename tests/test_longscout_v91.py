"""Formula and timing checks use synthetic data only; no economic tuning."""
import sys
from pathlib import Path
import math
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v91 as M


def daily(seed=5, n=150):
    rng = np.random.default_rng(seed)
    # Alternation exercises negative autocorrelation and both fade tails.
    ret = rng.normal(0, .02, n) + .06 * (-1.) ** np.arange(n)
    c = 100 * np.exp(ret.cumsum())
    o = np.r_[100., c[:-1]]
    return pd.DataFrame(dict(o=o, h=np.maximum(o, c)*1.015,
                             l=np.minimum(o, c)*.985, c=c),
                        index=pd.date_range('2022-01-01', periods=n))


def reference(D):
    p = M.PARAMS
    out = {k: np.zeros(len(D), bool) for k in ('impulse', 'range_long', 'range_short')}
    rows = list(D.itertuples())
    def raw(i):
        if i < 31: return False, False, False
        c = [r.c for r in rows]
        returns = [math.log(c[t]/c[t-1]) for t in range(i-30, i+1)]
        rho = np.corrcoef(returns[1:], returns[:-1])[0, 1]
        base = c[i-10:i]
        mu = sum(base)/10
        sd = math.sqrt(sum((x-mu)**2 for x in base)/9)
        rs = []
        for r in rows[i-20:i]:
            rs.append(math.log(r.h/r.o)*math.log(r.h/r.c)
                      + math.log(r.l/r.o)*math.log(r.l/r.c))
        sigma = math.sqrt(max(sum(rs)/20, 0))
        return (sigma>0 and math.log(c[i]/c[i-1])>1.5*sigma and c[i]>c[i-20],
                rho<=-.3 and sd>0 and c[i]<mu-sd,
                rho<=-.3 and sd>0 and c[i]>mu+sd)
    for i in range(32, len(D)):
        if D.index[i]-D.index[i-32] != pd.Timedelta(days=32): continue
        now, prev = raw(i), raw(i-1)
        out['impulse'][i] = now[0]
        out['range_long'][i] = now[1] and not prev[1]
        out['range_short'][i] = now[2] and not prev[2]
    return out


@pytest.mark.parametrize('seed', [1, 5, 21])
@pytest.mark.parametrize('gap', [False, True])
def test_independent_formula(seed, gap):
    D = daily(seed)
    if gap: D = D.drop(D.index[77])
    got, want = M.daily_conditions(D, M.PARAMS), reference(D)
    for key in want: np.testing.assert_array_equal(got[key], want[key])
    assert sum(want['range_long']) > 0
    assert sum(want['range_short']) > 0


def bars(D):
    rows = []
    for t, r in D.iterrows():
        for h in range(0, 24, 4):
            rows.append(dict(t=t+pd.Timedelta(hours=h), o=r.o, h=r.h, l=r.l, c=r.c, v=100.))
    return pd.DataFrame(rows)


@pytest.mark.parametrize('fn', [M.sig_impulse, M.sig_range])
def test_real_function_prefix_and_regimes(fn, monkeypatch):
    d = bars(daily())
    monkeypatch.setattr(M.L1, 'coin_regime', lambda x: np.zeros(len(x)))
    full = fn(d, M.PARAMS, {})
    assert full['le'].any() or full['se'].any()
    for k in ('le', 'se'):
        assert (d.t[full[k]].dt.hour == 0).all()
    for n in [193, 201, 370, 601, 802, 899]:
        part = fn(d.iloc[:n], M.PARAMS, {})
        for k in full: np.testing.assert_array_equal(part[k], full[k].iloc[:n])
    monkeypatch.setattr(M.L1, 'coin_regime', lambda x: np.full(len(x), -1.))
    falling = fn(d, M.PARAMS, {})
    assert not falling['le'].any() and not falling['se'].any()
    monkeypatch.setattr(M.L1, 'coin_regime', lambda x: np.full(len(x), 1.))
    rising = fn(d, M.PARAMS, {})
    if fn == M.sig_range: assert not rising['le'].any() and not rising['se'].any()
    else: np.testing.assert_array_equal(rising['le'], full['le'])


def test_incomplete_day_is_not_available(monkeypatch):
    d = bars(daily())
    monkeypatch.setattr(M.L1, 'coin_regime', lambda x: np.zeros(len(x)))
    D = M.L17._days(d)
    cond = M.daily_conditions(D, M.PARAMS)
    t = cond['range_long'][cond['range_long']].index[0]
    next_day = t+pd.Timedelta(days=1)
    assert M.sig_range(d, M.PARAMS, {})['le'][d.t == next_day].all()
    cut = d[d.t != t+pd.Timedelta(hours=12)].reset_index(drop=True)
    assert not M.sig_range(cut, M.PARAMS, {})['le'][cut.t == next_day].any()


def test_constant_and_invalid_data():
    D = daily()
    D.loc[:, ['o', 'h', 'l', 'c']] = 100.
    assert not any(x.any() for x in M.daily_conditions(D, M.PARAMS).values())
    D = daily()
    D.iloc[40, D.columns.get_loc('c')] = 0.
    with np.errstate(divide='ignore', invalid='ignore'):
        got = M.daily_conditions(D, M.PARAMS)
    for x in got.values(): assert not x.iloc[40:73].any()


def test_range_uses_prior_band_and_sample_sd():
    D = daily(21)
    got = M.daily_conditions(D, M.PARAMS)
    for stop in [40, 60, 91, 150]:
        part = M.daily_conditions(D.iloc[:stop], M.PARAMS)
        for k in got: np.testing.assert_array_equal(part[k], got[k].iloc[:stop])


def test_registration_is_opt_in():
    # Root strategies.py never imports this module; registration is runner-only.
    assert 'longscout_v91' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    M.register()
    for v in M.CANDIDATES.values():
        assert v['research_only'] is True
        assert v['tf'] == '4h'
        assert v['mgmt'] == dict(stop_atr=2.5, tp_r=2., max_bars=30)
