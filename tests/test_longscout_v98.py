"""Fabricated V98 signal, manifest and context-adapter checks; no market data."""
import ast
import copy
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'research')]
import longscout_v98 as M


def sample():
    dates=pd.date_range('2023-01-01',periods=45)
    d=pd.DataFrame(dict(t=dates,o=100.,c=100.,qv=100.))
    flow=pd.DataFrame(dict(trade_count=10.,taker_buy_qv=50.),index=dates)
    d.loc[25:26,'c']=101.1; flow.iloc[25:27,1]=39.
    d.loc[30:31,'qv']=200.; d.loc[30,'c']=94.; d.loc[31,'c']=88.
    flow.iloc[30:32,0]=40.
    d.loc[35,'qv']=200.; d.loc[35,'c']=106.; flow.iloc[35,0]=40.
    return d,flow


def reference(d,flow):
    rows=list(d.itertuples(index=False)); valid=[]; absor=[]; frag=[]; result={k:[] for k in ('absorption','long','short')}
    for i,r in enumerate(rows):
        f=flow.loc[r.t]; q=r.qv; count=f.trade_count; buy=f.taker_buy_qv
        ok=np.isfinite([q,count,buy,r.o,r.c]).all() and q>0 and count>0 and count==int(count) and 0<=buy<=q and r.o>0 and r.c>0
        valid.append(ok)
        ready=i>=21 and all(valid[i-21:i+1]) and all(rows[j].t-rows[j-1].t==pd.Timedelta(days=1) for j in range(i-20,i+1))
        a=bool(ready and Decimal(str(buy))*2 <= Decimal(str(q))*Decimal("0.8") and Decimal(str(r.c)) >= Decimal(str(r.o))*Decimal("1.01"))
        event=False; ret=0.
        if ready:
            qprev=[x.qv for x in rows[i-20:i]]
            sizes=[x.qv/flow.loc[x.t,'trade_count'] for x in rows[i-20:i]]
            ret=r.c/rows[i-1].c-1
            event=(Decimal(str(q))/Decimal(str(count)) <= Decimal("0.5")*Decimal(str(np.median(sizes))) and Decimal(str(q)) >= 2*Decimal(str(np.median(qprev))) and abs(Decimal(str(r.c))/Decimal(str(rows[i-1].c))-1)>=Decimal("0.05"))
        result['absorption'].append(a and not (absor[-1] if absor else False))
        edge=event and not (frag[-1] if frag else False)
        result['long'].append(edge and ret<0); result['short'].append(edge and ret>0)
        absor.append(a);frag.append(event)
    return result


def test_formula_reference_prefix_edges_gaps_and_invalid():
    d,f=sample(); got=M.daily_flags(d,f); want=reference(d,f)
    for k in want: np.testing.assert_array_equal(got[k],want[k])
    assert got['absorption'].iloc[25] and not got['absorption'].iloc[26]
    assert got['long'].iloc[30] and not got['long'].iloc[31] and got['short'].iloc[35]
    for n in (21,22,25,26,30,31,36,40):
        part=M.daily_flags(d.iloc[:n],f)
        for k in want: np.testing.assert_array_equal(part[k],got[k].iloc[:n])
    gap=d.drop(index=10).reset_index(drop=True)
    assert not M.daily_flags(gap,f)['absorption'].loc[d.t.iloc[25:27]].any()
    for col,value in [('trade_count',0),('trade_count',1.5),('taker_buy_qv',101),('taker_buy_qv',np.nan)]:
        g=f.copy();g.iloc[10,g.columns.get_loc(col)]=value
        assert not M.daily_flags(d,g)['absorption'].iloc[25]
    with pytest.raises(ValueError,match='missing'): M.daily_flags(d,f.iloc[:-1])


def test_actual_per_symbol_r1_and_native_daily(monkeypatch):
    d,f=sample(); quiet=f.copy(); quiet['taker_buy_qv']=50.
    monkeypatch.setattr(M,'_FLOW',{'AAA':f,'BBB':quiet})
    monkeypatch.setattr(M.L1,'regime_daily',lambda x:pd.Series(0.,index=x.index))
    full=M.sig_absorption(d,{},dict(research_symbol='AAA'))
    assert full['le'].iloc[25] and not M.sig_absorption(d,{},dict(research_symbol='BBB'))['le'].iloc[25]
    # This native daily signal is on the completed day; its fill is the next row's open.
    assert d.t.iloc[np.flatnonzero(full['le'])[0]+1]==d.t.iloc[25]+pd.Timedelta(days=1)
    for n in (25,26,27,31,36,45):
        part=M.sig_absorption(d.iloc[:n],{},dict(research_symbol='AAA'))
        for k in full: np.testing.assert_array_equal(part[k],full[k].iloc[:n])
    for r in (np.nan,-1.,1.):
        monkeypatch.setattr(M.L1,'regime_daily',lambda x,v=r:pd.Series(v,index=x.index))
        assert bool(M.sig_absorption(d,{},dict(research_symbol='AAA'))['le'].any()) == (r==1.)
        assert not M.sig_fragmentation(d,{},dict(research_symbol='AAA'))['le'].any()
        assert not M.sig_fragmentation(d,{},dict(research_symbol='AAA'))['se'].any()
    for ctx in ({}, {'research_symbol':'MISSING'}, {'research_symbol':None}):
        with pytest.raises(ValueError): M.sig_absorption(d,{},ctx)


def fixture_release(tmp_path):
    folder=tmp_path/'daily';folder.mkdir()
    names=['BTCUSDT']+[f'COIN{i:03}' for i in range(165)]
    blob=b't,trade_count,taker_buy_qv\n1672531200000,1,1\n'
    sha=hashlib.sha256(blob).hexdigest()
    for name in names: (folder/(name+'.csv')).write_bytes(blob)
    report={'status':'COMPLETE','probe':False,'symbols':{name:{'status':'PASS','daily_sha256':sha,'days':1} for name in names}}
    return report,{name:None for name in names}


def lock(tmp_path,monkeypatch,report):
    blob=json.dumps(report).encode(); (tmp_path/'report.json').write_bytes(blob)
    monkeypatch.setattr(M,'DATA_REPORT_SHA256',hashlib.sha256(blob).hexdigest())


def test_loader_fail_closed(tmp_path,monkeypatch):
    report,panel=fixture_release(tmp_path); monkeypatch.setattr(M,'DATA_DIR',tmp_path)
    monkeypatch.setattr(M,'DATA_REPORT_SHA256',None);monkeypatch.setattr(M,'_FLOW',{'old':1})
    with pytest.raises(RuntimeError): M.set_panel(panel)
    assert M._FLOW is None
    lock(tmp_path,monkeypatch,report);M.set_panel(panel); assert len(M._FLOW)==166
    for key,val in [('status','INCOMPLETE'),('probe',True)]:
        bad=copy.deepcopy(report);bad[key]=val;lock(tmp_path,monkeypatch,bad)
        with pytest.raises(ValueError): M.set_panel(panel)
        assert M._FLOW is None
    lock(tmp_path,monkeypatch,report)
    with pytest.raises(ValueError): M.set_panel(dict(list(panel.items())[:-1]))
    monkeypatch.setattr(M,'DATA_REPORT_SHA256','0'*64)
    with pytest.raises(ValueError): M.set_panel(panel)
    lock(tmp_path,monkeypatch,report)
    path=tmp_path/'daily'/'BTCUSDT.csv'; blob=path.read_bytes();path.write_bytes(blob+b'junk')
    with pytest.raises(ValueError): M.set_panel(panel)
    path.write_bytes(blob+blob.split(b'\n')[1]+b'\n')
    bad=copy.deepcopy(report);bad['symbols']['BTCUSDT']['daily_sha256']=hashlib.sha256(path.read_bytes()).hexdigest();bad['symbols']['BTCUSDT']['days']=2
    lock(tmp_path,monkeypatch,bad)
    with pytest.raises(ValueError,match='dates'): M.set_panel(panel)
    assert M._FLOW is None


def extract(path,name,env):
    tree=ast.parse(path.read_text(encoding='utf-8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(path),'exec'),env)
    return env[name]


def test_adapter_preserves_numeric_context_and_labels():
    original={'AAA':{'a':np.arange(6.),'b':pd.Series(np.arange(6)),'scalar':2.},'BBB':{'a':np.arange(6.)+10,'b':pd.Series(np.arange(6)+20),'scalar':3.}}
    before=copy.deepcopy(original)
    wrap=extract(ROOT/'research/extra31_screen/longscout_flow_run.py','instrument_context',{'original_context':lambda *a,**k: original})
    cut=extract(ROOT/'research/extra31_screen/sieve_v1_panel.py','cut_ctx',{})
    result=wrap({})
    for symbol,ctx in result.items():
        assert ctx['research_symbol']==symbol
        for key in before[symbol]:
            np.testing.assert_array_equal(np.asarray(ctx[key]),np.asarray(before[symbol][key]))
        clipped=cut(ctx,2,6)
        assert clipped['research_symbol']==symbol
        np.testing.assert_equal(clipped['a'],before[symbol]['a'][2:])
        pd.testing.assert_series_equal(clipped['b'],before[symbol]['b'].iloc[2:].reset_index(drop=True))


def test_default_off():
    assert M.DATA_REPORT_SHA256 is None
    assert 'longscout_v98' not in (ROOT/'strategies.py').read_text(encoding='utf-8')
    assert all(v['research_only'] and v['tf']=='1d' and v['mgmt']==dict(stop_atr=2.,tp_r=2.,max_bars=7) for v in M.CANDIDATES.values())


def test_exact_imbalance_threshold_is_inclusive():
    d,f=sample()
    f.iloc[25:27,1]=40.
    assert M.daily_flags(d,f)['absorption'].iloc[25]


def test_fragmentation_exact_boundaries_and_initial_22_days():
    d,f=sample()
    d.loc[30,'c']=95.
    d.loc[35,'c']=105.
    got=M.daily_flags(d,f)
    assert got['long'].iloc[30] and got['short'].iloc[35]
    for key,expected in reference(d,f).items(): np.testing.assert_array_equal(got[key],expected)
    d,f=sample()
    d.loc[21,'c']=101.
    f.iloc[21,1]=40.
    assert M.daily_flags(d,f)['absorption'].iloc[21]
    f.iloc[0,0]=0.
    assert not M.daily_flags(d,f)['absorption'].iloc[21]
