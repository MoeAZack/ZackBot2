"""Robustness breakdowns for the NEWSTRAT-CP survivors from the saved trade ledgers (no new backtest, no tuning).
Usage: python research/newstrat_cp_evidence.py <ledger_dir> <out_json>
R at a stress = (pnl - turnover * extra) / risk_usd per trade; 7 bp is the run itself, 15/23 bp add 8/16 bp per side."""
import json, sys, os
import pandas as pd

EXTRA = {7: 0.0, 15: 0.0008, 23: 0.0016}


def cell(t):
    out = dict(n=int(len(t)))
    for bp, x in EXTRA.items():
        r = (t.pnl - t.turnover * x) / t.risk_usd
        out[f'sumR_{bp}'] = round(float(r.sum()), 2); out[f'expR_{bp}'] = round(float(r.mean()), 4) if len(t) else None
    return out


def table(t, col):
    return {str(k): cell(g) for k, g in t.groupby(col)}


def main(src, out):
    res = {}
    for key in ('cp_n6_btc_lead_catchup_4h', 'cp_n8_squeeze_breakdown_short_4h'):
        for scen in ('path', 'worst'):
            t = pd.read_csv(os.path.join(src, f'{key}.{scen}.trades.csv'), parse_dates=['t_in'])
            t['year'] = t.t_in.dt.year; t['half'] = t.t_in.dt.year.astype(str) + 'H' + ((t.t_in.dt.month > 6) + 1).astype(str)
            t['period'] = (t.t_in >= '2025-01-01').map({False: 'DEV', True: 'HOLDOUT'})
            t['side_name'] = t.side.map({1: 'long', -1: 'short'})
            loo = {s: cell(t[t.sym != s]) for s in sorted(t.sym.unique())}
            byc = t.groupby('sym').apply(lambda g: ((g.pnl - g.turnover * EXTRA[15]) / g.risk_usd).sum())
            top = byc.idxmax()
            r15 = (t.pnl - t.turnover * EXTRA[15]) / t.risk_usd
            res[f'{key}.{scen}'] = dict(
                all=cell(t), period=table(t, 'period'), year=table(t, 'year'), half=table(t, 'half'),
                coin=table(t, 'sym'), side=table(t, 'side_name'), period_side=table(t.assign(ps=t.period + '/' + t.side_name), 'ps'),
                leave_one_coin_out=loo, without_best_coin=dict(coin=top, **cell(t[t.sym != top])),
                without_top5_trades=dict(**cell(t.drop(r15.nlargest(5).index))),
                positive_halves_15=int(sum(v['sumR_15'] > 0 for v in table(t, 'half').values())), halves=int(t.half.nunique()),
                positive_coins_15=int(sum(v['sumR_15'] > 0 for v in table(t, 'sym').values())), coins=int(t.sym.nunique()))
    json.dump(res, open(out, 'w', encoding='utf-8'), indent=1)
    for k, v in res.items():
        print(k, 'all', v['all'], '| halves+', v['positive_halves_15'], '/', v['halves'], '| coins+', v['positive_coins_15'], '/', v['coins'])
        print('   no best coin', v['without_best_coin']); print('   no top5', v['without_top5_trades'])
        print('   LOO min expR_15', min(x['expR_15'] for x in v['leave_one_coin_out'].values()))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
