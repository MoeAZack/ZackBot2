"""Independent synthetic V96 formula and causal-signal checks."""
import sys
from pathlib import Path
from collections import Counter
import itertools
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'research')]
import longscout_v96 as M


def bars(closes):
    return pd.DataFrame([(pd.Timestamp('2023-01-01')+pd.Timedelta(days=i,hours=h), float(c), float(c)+1, float(c)-1, float(c), 1.)
                         for i,c in enumerate(closes) for h in range(0,24,4)], columns=['t','o','h','l','c','v'])


def daily(closes):
    return pd.DataFrame({'c': np.asarray(closes,dtype=float)},index=pd.date_range('2023-01-01',periods=len(closes)))


def cusum_reference(D):
    dates=list(D.index); closes=list(D.c); n=len(D)
    r=[np.nan]*n; z=[np.nan]*n; score=[np.nan]*n; hits=[False]*n
    for i in range(1,n):
        if dates[i]-dates[i-1] == pd.Timedelta(days=1) and closes[i]>0 and closes[i-1]>0:
            r[i]=np.log(closes[i]/closes[i-1])
    for i in range(61,n):
        x=r[i-60:i]
        if np.isfinite(r[i]) and np.isfinite(x).all():
            mean=sum(x)/60
            sd=(sum((v-mean)**2 for v in x)/60)**.5
            if sd>1e-14: z[i]=(r[i]-mean)/sd
    for i in range(80,n):
        if all(np.isfinite(z[j]) for j in range(i-19,i+1)):
            score[i]=max([0.]+[sum(z[j]-.5 for j in range(i-k+1,i+1)) for k in range(1,21)])
        if i>=81 and dates[i]-dates[i-81] == pd.Timedelta(days=81):
            hits[i]=np.isfinite(score[i]) and np.isfinite(score[i-1]) and score[i]>=5 and score[i-1]<5
    return np.array(hits)


def ordinal_reference(D):
    entropy=np.full(len(D),np.nan); alt=np.full(len(D),np.nan); valid=np.full(len(D),np.nan)
    long=np.zeros(len(D),bool); short=np.zeros(len(D),bool); hit=np.zeros(len(D),bool)
    dates=list(D.index); c=list(D.c)
    for i in range(59,len(D)):
        patterns=[]; turns=0
        for j in range(i-57,i+1):
            x=c[j-2:j+1]
            if not np.isfinite(x).all() or len(set(x))<3: continue
            patterns.append(tuple(sorted(range(3),key=lambda k:x[k])))
            turns+=int((x[1]>x[0] and x[1]>x[2]) or (x[1]<x[0] and x[1]<x[2]))
        valid[i]=len(patterns)
        if patterns:
            freq=Counter(patterns); n=len(patterns)
            entropy[i]=-sum((v/n)*np.log(v/n) for v in freq.values())/np.log(6)
            alt[i]=turns/n
        if i>=60 and dates[i]-dates[i-60]==pd.Timedelta(days=60):
            hit[i]=valid[i]>=50 and entropy[i]<=.90 and alt[i]>=.75
        if hit[i] and not hit[i-1]:
            long[i]=c[i]<c[i-1]; short[i]=c[i]>c[i-1]
    return entropy,alt,valid,long,short


def cusum_data():
    rng=np.random.default_rng(961)
    r=rng.normal(0,.01,170)
    r[95:99]=.032
    r[135:139]=.034
    return 100*np.exp(np.cumsum(r))


def ordinal_data():
    rng=np.random.default_rng(962)
    c=100+np.cumsum(rng.normal(0,.3,145))
    for i in range(75,125): c[i]=100+(2 if i%2 else -2)+rng.uniform(-.1,.1)
    return c


def test_cusum_reference_warmup_gaps_and_constant():
    D=daily(cusum_data()); expected=cusum_reference(D)
    assert expected.any()
    np.testing.assert_array_equal(M.cusum(D),expected)
    assert not M.cusum(D.iloc[:81]).any()
    gap=D.drop(D.index[85]); np.testing.assert_array_equal(M.cusum(gap),cusum_reference(gap))
    assert not M.cusum(daily(np.full(110,100.))).any()
    missing=D.copy(); missing.iloc[85,0]=np.nan
    assert not M.cusum(missing).iloc[85:146].any()


def test_ordinal_reference_all_permutations_ties_and_gaps():
    # Every strict rank permutation, plus an extensive tie and an oscillating segment.
    pattern=list(itertools.chain.from_iterable(itertools.permutations((97.,100.,103.))))
    D=daily(np.r_[pattern,ordinal_data(),np.full(25,100.)]); ref=ordinal_reference(D)
    stats=M.ordinal_statistics(D)
    for key,want in zip(('entropy','alternate','valid'),ref[:3]):
        np.testing.assert_allclose(stats[key].iloc[59:],want[59:],atol=1e-12,equal_nan=True)
    got=M.ordinal(D)
    for key,want in zip(('long','short'),ref[3:]): np.testing.assert_array_equal(got[key],want)
    assert (ref[3] | ref[4]).any()
    gap=D.drop(D.index[100]); ref=ordinal_reference(gap); got=M.ordinal(gap)
    for key,want in zip(('long','short'),ref[3:]): np.testing.assert_array_equal(got[key],want)
    assert not M.ordinal(daily(np.full(90,100.)))['long'].any()
    assert not M.ordinal(daily(np.full(90,100.)))['short'].any()
    for n_ties in (4, 10):
        tied=daily(ordinal_data()); tied.iloc[90:90+n_ties,0]=100.
        ref=ordinal_reference(tied); stats=M.ordinal_statistics(tied)
        np.testing.assert_allclose(stats.valid.iloc[59:],ref[2][59:],equal_nan=True)
        for key,want in zip(('long','short'),ref[3:]): np.testing.assert_array_equal(M.ordinal(tied)[key],want)


@pytest.mark.parametrize('kind', ['cusum','ordinal'])
def test_actual_prefix_timing_and_regimes(kind,monkeypatch):
    d=bars(cusum_data() if kind=='cusum' else ordinal_data())
    fn=M.sig_cusum if kind=='cusum' else M.sig_ordinal
    monkeypatch.setattr(M.L1,'coin_regime',lambda x:np.zeros(len(x)))
    full=fn(d,{},{}); assert full['le'].any() or full['se'].any()
    assert d.t[full['le'] | full['se']].dt.hour.eq(0).all()
    flags=M.cusum(M.H.daily(d)) if kind=='cusum' else M.ordinal(M.H.daily(d))
    for k in ('le','se'):
        expected=flags if kind=='cusum' and k=='le' else (flags['long' if k=='le' else 'short'] if kind=='ordinal' else pd.Series(False,index=M.H.daily(d).index))
        expected_dates=set(expected.index[expected]+pd.Timedelta(days=1))
        assert set(d.t[full[k]]) == expected_dates.intersection(set(d.t))
    for end in (480,486,487,540,600,720,800):
        if end>len(d): continue
        part=fn(d.iloc[:end],{},{})
        for k in full: np.testing.assert_array_equal(part[k],full[k].iloc[:end])
    for regime in (np.nan,-1.,1.):
        monkeypatch.setattr(M.L1,'coin_regime',lambda x,r=regime:np.full(len(x),r))
        out=fn(d,{},{})
        assert bool((out['le'] | out['se']).any()) == (kind=='cusum' and regime==1.)


def test_default_off_registration_and_management(monkeypatch):
    assert 'longscout_v96' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    for k in M.CANDIDATES: monkeypatch.delitem(M.S.STRATEGIES,k,raising=False)
    assert not any(k in M.S.STRATEGIES for k in M.CANDIDATES)
    M.register()
    for k,v in M.CANDIDATES.items():
        assert M.S.STRATEGIES[k]['research_only'] and v['tf']=='4h'
        assert v['mgmt']==dict(stop_atr=2.5,tp_r=2.,max_bars=30)
        monkeypatch.delitem(M.S.STRATEGIES,k)


def test_ordinal_fifty_valid_triples_boundary():
    rng=np.random.default_rng(963)
    found={}
    for _ in range(300):
        c=np.array([(102. if i%2 else 98.) + i*.001 for i in range(61)])
        for i in rng.choice(np.arange(2,60),size=int(rng.choice([4,5])),replace=False): c[i]=c[i-1]
        n=sum(len(set(c[j-2:j+1]))==3 for j in range(3,61))
        if n not in (49,50): continue
        D=daily(c); entropy,alt,valid,long,short=ordinal_reference(D)
        if n in (49,50) and entropy[-1]<=.9 and alt[-1]>=.75 and c[-1]!=c[-2]:
            found[n]=(D, bool(long[-1] or short[-1]))
        if len(found)==2: break
    assert set(found)=={49,50}
    for n,(D,want) in found.items():
        got=M.ordinal(D)
        assert bool(got['long'].iloc[-1] or got['short'].iloc[-1]) == want == (n==50)

