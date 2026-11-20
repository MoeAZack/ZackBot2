"""Synthetic causal and formula checks; no market data."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v92 as M


def frame(n=45, pivot=35, rising=True):
    rows = []
    for j in range(n):
        c = 100. if j < pivot or not rising else 102.
        for h in range(0, 24, 4):
            rows.append((pd.Timestamp('2023-01-01')+pd.Timedelta(days=j,hours=h),
                         c, c+2, c-2, c, 1., 2e6))
    return pd.DataFrame(rows, columns=['t','o','h','l','c','v','qv'])


def reference_breadth(panel):
    days = sorted({t.normalize() for s,d in panel.items() if s != 'BTCUSDT' for t in d.t})
    grouped = {s: {day: (list(g.t.dt.hour), g.qv.sum(min_count=6), g.c.iloc[-1])
                   for day,g in d.groupby(d.t.dt.normalize())}
               for s,d in panel.items() if s != 'BTCUSDT'}
    per = {}
    for day in days:
        vals = []
        for by_day in grouped.values():
            parts = []
            for k in range(30,-1,-1):
                dd = day-pd.Timedelta(days=k)
                g = by_day.get(dd)
                if g is None or g[0] != [0,4,8,12,16,20]:
                    break
                parts.append(g)
            if len(parts) != 31:
                continue
            q = [x[1] for x in parts[:-1]]
            if np.median(q) < 1e7 or not np.isfinite(np.median(q)):
                continue
            now, prior = parts[-1][2], parts[-11][2]
            if not np.isfinite(now/prior):
                continue
            vals.append(now > prior)
        per[day] = (sum(vals)/len(vals) if vals else np.nan, len(vals))
    return pd.Series({day: (per[day][1]>=50 and per.get(day-pd.Timedelta(days=1),(np.nan,0))[1]>=50
                            and per[day][0]>=.65 and per.get(day-pd.Timedelta(days=1),(np.nan,0))[0]<.65)
                      for day in days})


def panel(n=50):
    return {f'COIN{i:03}':frame(rising=i<33) for i in range(n)}


def test_breadth_reference_liquidity_ties_gap_and_btc():
    P = panel()
    P['BTCUSDT'] = frame(rising=True)
    P['COIN049'].loc[P['COIN049'].t.dt.day.eq(2), 'qv'] = 1e4
    P['COIN048'] = P['COIN048'].loc[~P['COIN048'].t.eq(pd.Timestamp('2023-01-12 12:00'))].reset_index(drop=True)
    got = M.breadth(P, M.PARAMS)
    want = reference_breadth(P)
    np.testing.assert_array_equal(got.reindex(want.index,fill_value=False), want)
    assert not got.any()  # only 49 peers eligible at the intended crossing
    del P['COIN048']
    P['COIN048'] = frame(rising=False)
    got = M.breadth(P, M.PARAMS)
    np.testing.assert_array_equal(got.reindex(want.index,fill_value=False), reference_breadth(P))
    assert got.loc[pd.Timestamp('2023-02-05')]
    assert M.breadth({k:v for k,v in P.items() if k != 'BTCUSDT'},M.PARAMS).equals(got)


def test_prior_liquidity_boundary():
    P = panel()
    for d in P.values():
        d.loc[d.t.dt.normalize().eq(pd.Timestamp('2023-02-05')), 'qv'] = 0
    assert M.breadth(P, M.PARAMS).loc[pd.Timestamp('2023-02-05')]
    for d in P.values():
        d.loc[d.t.dt.normalize().lt(pd.Timestamp('2023-02-05')), 'qv'] = 0
    assert not M.breadth(P, M.PARAMS).any()


def path_frame():
    d=frame(n=8)
    for j in [2,3,6]:
        ix=d.index[d.t.dt.normalize().eq(pd.Timestamp('2023-01-01')+pd.Timedelta(days=j))]
        d.loc[ix,'o']=102.; d.loc[ix,'c']=101.; d.loc[ix,'h']=104.; d.loc[ix,'l']=99.
        d.loc[ix[0], 'l']=96.; d.loc[ix[3], 'l']=96.  # earliest tied low
    ix=d.index[d.t.dt.normalize().eq(pd.Timestamp('2023-01-05'))]
    d.loc[ix,'o']=98.; d.loc[ix,'c']=99.; d.loc[ix,'h']=102.; d.loc[ix,'l']=96.
    d.loc[ix[1], 'h']=104.; d.loc[ix[4], 'h']=104.  # earliest tied high
    return d


def path_reference(d):
    raw={}
    for day,g in d.groupby(d.t.dt.normalize()):
        if list(g.t.dt.hour) != [0,4,8,12,16,20]:
            continue
        low, high = min(g.l),max(g.h)
        mid=(low+high)/2
        raw[day]=(list(g.l).index(low)<2 and g.c.iloc[-1]>mid and g.c.iloc[-1]<g.o.iloc[0],
                  list(g.h).index(high)<2 and g.c.iloc[-1]<mid and g.c.iloc[-1]>g.o.iloc[0])
    out={}
    for day, now in raw.items():
        prev=raw.get(day-pd.Timedelta(days=1))
        out[day]=tuple(prev is not None and now[i] and not prev[i] for i in range(2))
    return out


@pytest.mark.parametrize('missing',[False,True])
def test_path_reference_tie_first_occurrence_and_gap(missing):
    d=path_frame()
    if missing:
        d=d.loc[~d.t.eq(pd.Timestamp('2023-01-05 12:00'))].reset_index(drop=True)
    D=M.daily(d); got=M.path_conditions(D); want=path_reference(d)
    for day, (lo,hi) in want.items():
        assert bool(got['long'].loc[day]) == lo
        assert bool(got['short'].loc[day]) == hi
    assert bool(got['long'].loc[pd.Timestamp('2023-01-03')])
    assert not bool(got['long'].loc[pd.Timestamp('2023-01-04')])
    if missing: assert pd.Timestamp('2023-01-05') not in D.index
    else: assert bool(got['short'].loc[pd.Timestamp('2023-01-05')])


@pytest.mark.parametrize('which',['breadth','path'])
def test_signals_prefix_hours_and_regimes(which,monkeypatch):
    P=panel(); d=P['COIN000'] if which=='breadth' else path_frame()
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.zeros(len(x)))
    M.set_panel(P)
    fn=M.sig_breadth if which=='breadth' else M.sig_path
    p=M.PARAMS if which=='breadth' else {}
    full=fn(d,p,{})
    assert full['le'].any()
    assert (d.t[full['le']].dt.hour==0).all()
    expected = pd.Timestamp('2023-02-06' if which=='breadth' else '2023-01-04')
    assert bool(full['le'][d.t.eq(expected)].iloc[0])
    for end in ([209,212,240] if which=='breadth' else [18,20,30,40]):
        partial=d.iloc[:end]
        M.set_panel({k:v.iloc[:end] for k,v in P.items()})
        got=fn(partial,p,{})
        for k in full: np.testing.assert_array_equal(got[k], full[k].iloc[:end])
    M.set_panel(P)
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.full(len(x),-1.))
    assert not fn(d,p,{})['le'].any()
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.full(len(x),1.))
    assert fn(d,p,{})['le'].any() == (which=='breadth')


def test_default_off_and_management():
    assert 'longscout_v92' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for v in M.CANDIDATES.values():
        assert v['research_only'] and v['tf']=='4h'
        assert v['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
