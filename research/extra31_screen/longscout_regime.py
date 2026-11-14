"""LONGSCOUT-V1 regime split (frozen with longscout_v1_cases.json, before any split was looked at). RESEARCH ONLY.

Reads a longscout_run.py output (<case>_all_orders_trades.csv.gz, path scenario) and labels every closed trade with
regime R1 (research/longscout_v1.py) of BTC and of the traded coin at the trade's decision time (the entry bar's open =
the signal bar's close). Reports per regime x side: closes, mean R15, sum R15 for order 0 and the median over orders,
plus the share of 4h bars each coin spent in each regime from T0 (base rates).
  python research/extra31_screen/longscout_regime.py <clean_csv_dir> <run_out_dir> --case NAME [--tf 4h]
"""
import sys, os, json, argparse
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, 'research')); sys.path.insert(0, ROOT)
import run_screen as RS, sieve_v1 as SV
import longscout_v1 as L

NAMES = {1.0: 'rising', 0.0: 'sideways', -1.0: 'falling'}


def regime_at(raw4, ts):
    """R1 of a coin (its 4h frame) known at each time in ts (a day counts once its close day+1 <= t)."""
    import numpy as np, pandas as pd
    r = L.regime_daily(L.daily_closes(raw4.t, raw4.c))
    avail = pd.DatetimeIndex(r.index) + pd.Timedelta(days=1)
    pos = avail.searchsorted(pd.DatetimeIndex(ts), side='right') - 1
    v = np.where(pos >= 0, r.values[np.clip(pos, 0, None)], np.nan)
    return [NAMES.get(x, 'unknown') if x == x else 'unknown' for x in v]


def table(tr, col):
    import numpy as np
    out = {}
    for (reg, side), g in tr.groupby([col, 'side_name']):
        per = g.groupby('order').R15.agg(['size', 'mean', 'sum'])
        o0 = g[g.order == 0]
        out[f'{reg}|{side}'] = dict(order0_n=int(len(o0)), order0_meanR15=float(o0.R15.mean()) if len(o0) else None,
                                    order0_sumR15=float(o0.R15.sum()), median_n=float(per['size'].median()),
                                    median_meanR15=float(per['mean'].median()), median_sumR15=float(per['sum'].median()),
                                    orders_with_trades=int(len(per)))
    return out


def main():
    import numpy as np, pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument('data'); ap.add_argument('run'); ap.add_argument('--case', required=True); ap.add_argument('--tf', default='4h')
    a = ap.parse_args()
    tr = pd.read_csv(os.path.join(a.run, f'{a.case}_all_orders_trades.csv.gz'), parse_dates=['t_in', 't_out'])
    tr = tr[tr.scen == 'path'].copy()
    tr['R15'] = tr.pnl15 / SV.RISK_USD
    tr['side_name'] = np.where(tr.side == 1, 'long', 'short')
    syms = sorted(set(tr.sym) | {SV.BTC})
    raw4 = {s: RS.to_tf(RS.load_1h(a.data, s), '4h') for s in syms}
    tr['btc_regime'] = regime_at(raw4[SV.BTC], tr.t_in)
    tr['coin_regime'] = 'unknown'
    for s, idx in tr.groupby('sym').groups.items():
        tr.loc[idx, 'coin_regime'] = regime_at(raw4[s], tr.loc[idx, 't_in'])
    tr['half'] = np.where(tr.t_in < pd.Timestamp(SV.SPLIT), 'early', 'late')
    base = {}
    for s in syms:
        if s == SV.BTC and s not in set(tr.sym): continue
        t = raw4[s].t[raw4[s].t >= pd.Timestamp(SV.T0)]
        lab = pd.Series(regime_at(raw4[s], t + pd.Timedelta(hours=4)))
        base[s] = lab.value_counts(normalize=True).round(4).to_dict()
    pooled = pd.DataFrame(base).T.fillna(0).mean().round(4).to_dict()
    tb = raw4[SV.BTC].t[raw4[SV.BTC].t >= pd.Timestamp(SV.T0)]
    btc_base = pd.Series(regime_at(raw4[SV.BTC], tb + pd.Timedelta(hours=4))).value_counts(normalize=True).round(4).to_dict()
    side = {}
    for sd, g in tr.groupby('side_name'):
        per = g.groupby('order').R15.agg(['size', 'mean', 'sum'])
        side[sd] = dict(median_n=float(per['size'].median()), median_meanR15=float(per['mean'].median()),
                        median_sumR15=float(per['sum'].median()),
                        by_half={h: dict(median_sumR15=float(x.groupby('order').R15.sum().median()), n_order0=int((x.order == 0).sum()))
                                 for h, x in g.groupby('half')})
    out = dict(case=a.case, orders=int(tr.order.nunique()), closes_order0=int((tr.order == 0).sum()),
               by_side=side, by_btc_regime=table(tr, 'btc_regime'), by_coin_regime=table(tr, 'coin_regime'),
               time_share_coin_regime_mean_over_coins=pooled, time_share_btc_regime=btc_base,
               time_share_per_coin=base, regime_rule='R1 (research/longscout_v1.py)', split=SV.SPLIT)
    json.dump(out, open(os.path.join(a.run, f'{a.case}_regime.json'), 'w', encoding='utf-8'), indent=1)
    tr.to_csv(os.path.join(a.run, f'{a.case}_regime_trades.csv.gz'), index=False, encoding='utf-8')
    print(json.dumps({k: out[k] for k in ('by_side', 'time_share_coin_regime_mean_over_coins', 'time_share_btc_regime')}, indent=1))
    for col in ('by_btc_regime', 'by_coin_regime'):
        print(col)
        for k, v in sorted(out[col].items()):
            print(f'  {k:22s} o0 n={v["order0_n"]:4d} sumR15={v["order0_sumR15"]:8.2f} | med n={v["median_n"]:6.1f} meanR15={v["median_meanR15"]:+.3f} sumR15={v["median_sumR15"]:+8.2f}')


if __name__ == '__main__':
    main()
