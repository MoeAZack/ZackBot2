"""Recompute every CX rule-v1 and subset gate from all-order saved tapes.

Arithmetic reconciliation only, not independent execution reproduction.
Usage: python .../longscout_cx_audit.py RESULT_ROOT FRESH_SELECTED_JSON
"""
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd


def audit(root, selected, cases=None):
    fresh=set(json.loads(Path(selected).read_text())['selected'])
    extra=set(json.loads((Path(__file__).parent/'contract.json').read_text())['universe']['extra31'])
    assert len(fresh)==134 and len(extra)==31 and not fresh.intersection(extra)
    if cases is None:
        cases=sorted(p.name for p in Path(root).glob('cx*') if (p/(p.name+'_result.json')).is_file())
    assert cases, 'no completed cases'
    rows=[]
    for case in cases:
        p=Path(root)/case
        result=json.loads((p/(case+'_result.json')).read_text())
        try:
            tape=pd.read_csv(p/(case+'_all_orders_trades.csv.gz'))
        except pd.errors.EmptyDataError:
            tape=pd.DataFrame(columns=['sym','order','scen','t_in','pnl7','pnl15','pnl23'])
        assert set(tape.sym).issubset(fresh|extra)
        assert set(tape.order).issubset(range(40)) and set(tape.scen).issubset({'path','worst'})
        medians={}; stats=[]
        for order in range(40):
            t=tape[(tape.order==order)&(tape.scen=='path')].copy()
            early=t[pd.to_datetime(t.t_in)<pd.Timestamp('2025-01-01')]
            late=t[pd.to_datetime(t.t_in)>=pd.Timestamp('2025-01-01')]
            best=t.groupby('sym').pnl15.sum().idxmax() if len(t) else None
            mean=lambda x,c:float(x[c].mean()/8) if len(x) else None
            stats.append(dict(n=len(t),meanR7=mean(t,'pnl7'),meanR15=mean(t,'pnl15'),meanR23=mean(t,'pnl23'),
                meanR15_early=mean(early,'pnl15'),meanR15_late=mean(late,'pnl15'),
                meanR15_ex_best=mean(t[t.sym!=best],'pnl15'),n_early=len(early),n_late=len(late)))
        for k in ('n','meanR7','meanR15','meanR23','meanR15_early','meanR15_late','meanR15_ex_best'):
            values=[s[k] for s in stats]
            medians[k]=float(np.median(values)) if all(v is not None for v in values) else None
            saved=result['medians'][k]
            assert saved is None if medians[k] is None else np.isclose(medians[k],saved,rtol=1e-10,atol=1e-12),(case,k)
        positive=lambda k:medians[k] is not None and medians[k]>0
        gates=dict(trades=medians['n']>=100,meanR15=positive('meanR15'),early=positive('meanR15_early'),
            late=positive('meanR15_late'),ex_best_coin=positive('meanR15_ex_best'),
            meanR23=medians['meanR23'] is not None and medians['meanR23']>=0,
            order0_30_per_half=stats[0]['n_early']>=30 and stats[0]['n_late']>=30)
        assert gates==result['gates'],(case,gates,result['gates'])
        subsets={}; counts={}
        for label,mask in [('fresh134',tape.sym.isin(fresh)),('extra31',tape.sym.isin(extra))]:
            sub=tape[mask&(tape.scen=='path')]
            values=sub.groupby('order').pnl15.mean().reindex(range(40))/8
            subsets[label]=None if values.isna().any() else float(values.median())
            counts[label]=float(sub.groupby('order').size().reindex(range(40),fill_value=0).median())
            saved_subset=result['wide165']['subset_gate'][label]
            saved=saved_subset['median_meanR15'] if isinstance(saved_subset,dict) else None
            assert saved is None if subsets[label] is None else np.isclose(subsets[label],saved,rtol=1e-10,atol=1e-12)
        passed=all(gates.values()) and all(v is not None and v>0 for v in subsets.values())
        status='WIDE165_PASS' if passed else 'FAIL'
        assert status==result['wide165']['status']
        rows.append(dict(id=case,status=status,**medians,**subsets,subset_n=counts,
            order0_early=stats[0]['n_early'],order0_late=stats[0]['n_late'],failed=[k for k,v in gates.items() if not v]+
            [k for k,v in subsets.items() if v is None or v<=0]))
    return rows


if __name__=='__main__':
    print(json.dumps(audit(sys.argv[1],sys.argv[2]),indent=2))
