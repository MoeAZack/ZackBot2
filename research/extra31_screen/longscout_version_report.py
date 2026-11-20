"""Descriptive post-run diagnostics, never an alpha tuning tool. Reads own batch only."""
import json
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'research'), str(Path(__file__).parent)]
import longscout_v1 as L1
import run_screen as RS
import sieve_v1 as SV


def dd_label(x):
    return 'Standard' if x <= 15 else 'Elevated' if x <= 30 else 'High' if x <= 60 else 'Extreme'


def main():
    out = Path(sys.argv[1])
    audit = json.loads((out/'input_audit.json').read_text())
    cases = json.loads((Path(__file__).parent/os.environ.get('LONGSCOUT_CASES','longscout_v91_cases.json')).read_text())
    fresh = set(json.loads((Path(sys.argv[2])/'selected.json').read_text())['selected'])
    btc = RS.to_tf(RS.load_1h(str(out/'_data'), 'BTCUSDT'), '4h')
    regimes = pd.Series(L1.coin_regime(btc), index=pd.DatetimeIndex(btc.t))
    report = dict(note='Descriptive diagnostics on an already-consumed calendar, not untouched or forward evidence.', cases={})
    for key in cases['cases']:
        r = json.loads((out/key/f'{key}_result.json').read_text())
        tape = pd.read_csv(out/key/f'{key}_all_orders_trades.csv.gz', parse_dates=['t_in','t_out'])
        tr = tape[tape.scen == 'path'].copy()
        # All frozen candidates enter on the bar after their signal bar.
        tr['btc_regime_at_signal'] = regimes.reindex(pd.DatetimeIndex(tr.t_in)-pd.Timedelta(hours=4)).to_numpy()
        dds = []
        for order in range(40):
            x = tr[tr.order == order].sort_values('t_out')
            e = np.r_[0., (x.pnl15/8).cumsum().to_numpy()]
            dds.append(float((np.maximum.accumulate(e)-e).max()))
        def group(mask):
            x = tr[mask]
            means = x.groupby('order').pnl15.mean().reindex(range(40))/8
            return dict(median_n=float(x.groupby('order').size().reindex(range(40),fill_value=0).median()),
                        median_meanR15=None if means.isna().any() else float(means.median()),
                        positive_orders=int((means>0).sum()))
        diag = dict(status=r['wide165']['status'], medians=r['medians'], gates=r['gates'],
                    subsets=r['wide165']['subset_gate'], cost_model=r['v91_costs'],
                    dd=dict(order0_R=dds[0], order0_label=dd_label(dds[0]), median_R=float(np.median(dds)),
                            median_label=dd_label(float(np.median(dds))), max_R=max(dds), max_label=dd_label(max(dds)),
                            per_order_R=dds,note='Closed equity, initial zero included; not mark-to-market DD.'),
                    btc_regimes={name:group(tr.btc_regime_at_signal == value) for name,value in [('rising',1.),('sideways',0.),('falling',-1.)]},
                    sides={name:group(tr.side == value) for name,value in [('long',1),('short',-1)]},
                    yearly={str(y):group(tr.t_in.dt.year == y) for y in sorted(tr.t_in.dt.year.unique())},
                    missing_rule_trades=dict(symbols=audit['missing_exchange_rules'],
                                            path_trades_all_orders=int(tr.sym.isin(audit['missing_exchange_rules']).sum()),
                                            order0=int((tr.sym.isin(audit['missing_exchange_rules']) & (tr.order == 0)).sum())),
                    positive_orders=int((tr.groupby('order').pnl15.sum()>0).sum()))
        # Independently reconstruct gates and subsets from the saved closed-trade tape.
        stats = [SV.order_stats(tr[tr.order == order]) for order in range(40)]
        med, gates = SV.gates(stats)
        assert gates == r['gates'], key
        for k,v in med.items():
            assert (v is None and r['medians'][k] is None) or np.isclose(v,r['medians'][k],rtol=1e-12,atol=1e-12),(key,k)
        for name,keep in [('fresh134',tr.sym.isin(fresh)),('extra31',tr.sym.isin(RS.EXTRA31))]:
            actual = group(keep)['median_meanR15']
            expected = r['wide165']['subset_gate'][name]['median_meanR15']
            assert (actual is None and expected is None) or np.isclose(actual,expected,rtol=1e-12,atol=1e-12),(key,name)
        diag['saved_tape_gate_reconstruction'] = 'MATCH; not independent price replay'
        report['cases'][key] = diag
    (out/'diagnostics.json').write_text(json.dumps(report,indent=1)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=1))


if __name__ == '__main__':
    main()
