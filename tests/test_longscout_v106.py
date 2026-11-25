"""Synthetic-only independent checks for V106 native-daily signals."""
import math
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v106 as M


PLANTED_MUTANTS = [
    ('wrong_target_weekday', '(6,13,20,27,34,41,48,55)', '(5,12,19,26,33,40,47,54)',
     'test_weekday_independent_prior_index_and_real_signal'),
    ('invert_transaction_vwap', 'D.qv / D.v', 'D.v / D.qv',
     'test_daily_vwap_arithmetic_edges_invalid_gaps_and_regime'),
    ('allow_falling_weekday', 'np.isin(regime,(0.,1.))', 'np.isfinite(regime)',
     'test_weekday_independent_prior_index_and_real_signal'),
]


def fixture(n=72):
    ret=np.array([.01 if i%7==2 else -.001 for i in range(n)])
    close=100*np.exp(np.cumsum(ret));t=pd.date_range('2023-01-01',periods=n,freq='D')
    return pd.DataFrame({'t':t,'o':close,'h':close*1.1,'l':close*.9,'c':close,'v':100.,'qv':100*close})


def reference(D,j):
    closes=list(D.c);indices=[j-6-7*k for k in range(8)]
    r=[math.log(closes[k]/closes[k-1]) for k in indices]
    return sum(x>0 for x in r),sum(r)/8


def test_weekday_independent_prior_index_and_real_signal(monkeypatch):
    d=fixture();D=M.native_days(d);st=M.weekday_statistics(D);j=57
    count,mean=reference(D,j)
    assert count==8 and mean==pytest.approx(.01)
    assert st['count'].iloc[j]==8 and st.positive.iloc[j]==count and st['mean'].iloc[j]==pytest.approx(mean)
    assert not M.weekday_flags(D).iloc[:j].any() and M.weekday_flags(D).iloc[j]
    # Oldest used return and the target weekday are explicit and prior-only.
    assert all(D.index[j+1].weekday()==D.index[j-6-7*k].weekday() for k in range(8))
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(0.,index=c.index))
    signal=M.sig_weekday(d,{}, {})['le'];assert signal.iloc[j]
    assert d.t.iloc[j]+pd.Timedelta(days=1)==D.index[j+1]
    altered=d.copy();altered.loc[j+1:,'c']*=2
    pd.testing.assert_series_equal(signal.iloc[:j+1],M.sig_weekday(altered,{}, {})['le'].iloc[:j+1])
    for cut in (57,58,59,65):
        out=M.sig_weekday(d.iloc[:cut],{}, {})
        np.testing.assert_array_equal(out['le'],signal.iloc[:cut])
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(-1.,index=c.index))
    assert not M.sig_weekday(d,{}, {})['le'].any()
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(1.,index=c.index))
    assert M.sig_weekday(d,{}, {})['le'].iloc[j]


def test_daily_vwap_arithmetic_edges_invalid_gaps_and_regime(monkeypatch):
    d=fixture();d.loc[5,'qv']=d.loc[5,'v']*d.loc[5,'c']/0.96
    d.loc[8,'qv']=d.loc[8,'v']*d.loc[8,'c']/1.04
    D=M.native_days(d);st=M.vwap_statistics(D)
    assert st.vwap.iloc[5]==pytest.approx(d.qv.iloc[5]/d.v.iloc[5])
    flags=M.vwap_flags(D);assert flags['long'].iloc[5] and flags['short'].iloc[8]
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(0.,index=c.index))
    out=M.sig_vwap(d,{}, {});assert out['le'].iloc[5] and out['se'].iloc[8]
    for cut in (5,6,8,9):
        part=M.sig_vwap(d.iloc[:cut],{}, {})
        for key in out:np.testing.assert_array_equal(part[key],out[key].iloc[:cut])
    monkeypatch.setattr(M.L1,'regime_daily',lambda c:pd.Series(1.,index=c.index))
    assert not M.sig_vwap(d,{}, {})['le'].any() and not M.sig_vwap(d,{}, {})['se'].any()
    for key,value in [('v',0),('qv',0),('v',float('nan')),('qv',-1),('c',0)]:
        bad=d.copy();bad.loc[4,key]=value
        assert not M.vwap_flags(M.native_days(bad))['long'].iloc[5]
    missing=d.drop(index=4).reset_index(drop=True);res=M.vwap_flags(M.native_days(missing))
    assert not res['long'].iloc[4]


def test_gap_readiness_bad_time_and_default_off():
    d=fixture();gap=d.drop(index=30).reset_index(drop=True)
    assert not M.weekday_flags(M.native_days(gap)).any()
    for operation in ('duplicate','nonnative','reversed'):
        bad=d.copy()
        if operation=='duplicate':bad.loc[1,'t']=bad.loc[0,'t']
        if operation=='nonnative':bad.loc[1,'t']+=pd.Timedelta(hours=4)
        if operation=='reversed':bad=bad.iloc[::-1]
        with pytest.raises(ValueError):M.native_days(bad)
    assert 'longscout_v106' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for key,v in M.CANDIDATES.items():assert key not in M.S.STRATEGIES and v['research_only'] and v['tf']=='1d'


@pytest.mark.parametrize('unit',['s','ms','us','ns'])
def test_native_timestamp_resolutions(unit):
    d=fixture();d['t']=d.t.dt.as_unit(unit)
    assert M.weekday_flags(M.native_days(d)).iloc[57]
