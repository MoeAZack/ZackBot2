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


def forward_bar(path, status, median_dd):
    orders = range(40)
    grouped = path.groupby('order')
    mean_stress = grouped.pnl23.mean().reindex(orders) / SV.RISK_USD
    net = grouped.pnl15.sum().reindex(orders)
    top_three = grouped.pnl15.apply(lambda pnl: pnl[pnl > 0].nlargest(3).sum()).reindex(orders)
    concentration = top_three / net.where(net > 0)
    regimes = {}
    for name, value in [('rising', 1.), ('sideways', 0.), ('falling', -1.)]:
        means = path[path.btc_regime_at_signal == value].groupby('order').pnl15.mean().reindex(orders) / SV.RISK_USD
        regimes[name] = None if means.isna().any() else float(means.median())
    stress = None if mean_stress.isna().any() else float(mean_stress.median())
    top3 = None if concentration.isna().any() else float(concentration.median())
    gates = dict(wide_screen=status == 'WIDE165_PASS', rising=(regimes['rising'] or 0) > 0,
                 sideways=(regimes['sideways'] or 0) > 0, stress=stress is not None and stress >= 0,
                 drawdown=median_dd <= 30, top3=top3 is not None and top3 < 0.5)
    return dict(qualified=all(gates.values()), gates=gates, fresh_regime_R15=regimes,
                fresh_R23=stress, fresh_top3_share=top3)


def main():
    out = Path(sys.argv[1])
    audit = json.loads((out/'input_audit.json').read_text())
    cases = json.loads((Path(__file__).parent/os.environ.get('LONGSCOUT_CASES','longscout_v91_cases.json')).read_text())
    fresh = set(json.loads((Path(sys.argv[2])/'selected.json').read_text())['selected'])
    btc1 = RS.load_1h(str(out/'_data'), 'BTCUSDT')
    report = dict(note='Descriptive diagnostics on an already-consumed calendar, not untouched or forward evidence.', cases={})
    for key in cases['cases']:
        tf=cases['cases'][key]['tf']
        btc=RS.to_tf(btc1,tf)
        regimes=pd.Series(L1.coin_regime(btc),index=pd.DatetimeIndex(btc.t))
        r = json.loads((out/key/f'{key}_result.json').read_text())
        try:
            tape = pd.read_csv(out/key/f'{key}_all_orders_trades.csv.gz', parse_dates=['t_in','t_out'])
        except pd.errors.EmptyDataError:
            tape = pd.DataFrame({k:pd.Series(dtype=dt) for k,dt in
                                 [('sym','str'),('side','int64'),('t_in','datetime64[ns]'),('t_out','datetime64[ns]'),
                                  ('pnl15','float64'),('pnl23','float64'),('order','int64'),('scen','str')]})
        tr = tape[tape.scen == 'path'].copy()
        # All frozen candidates enter on the bar after their signal bar.
        tr['btc_regime_at_signal'] = regimes.reindex(pd.DatetimeIndex(tr.t_in)-pd.Timedelta(hours=RS.BAR_H[tf])).to_numpy()
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
        subsets=dict(r['wide165']['subset_gate'])
        for subset in ('fresh134','extra31'):
            if subsets.get(subset) is None:subsets[subset]=dict(median_meanR15=None,median_n=0.)
        diag = dict(status=r['wide165']['status'], medians=r['medians'], gates=r['gates'],
                    subsets=subsets, cost_model=r['v91_costs'], no_closed_trades=not len(tr),
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
            raw_subset=r['wide165']['subset_gate'][name]
            expected=raw_subset['median_meanR15'] if isinstance(raw_subset,dict) else None
            assert (actual is None and expected is None) or np.isclose(actual,expected,rtol=1e-12,atol=1e-12),(key,name)
        diag['saved_tape_gate_reconstruction'] = 'MATCH; not independent price replay'
        if 'forward_bar' in cases:
            diag['forward_bar'] = forward_bar(tr[tr.sym.isin(fresh)], diag['status'], diag['dd']['median_R'])
        report['cases'][key] = diag
    (out/'diagnostics.json').write_text(json.dumps(report,indent=1)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=1))


if __name__ == '__main__':
    main()
