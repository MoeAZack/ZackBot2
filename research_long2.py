"""Second batch on the long history: Active (1h) alternatives, bull-only on every 4h preset, MC (daily blocks) and
walk-forward on the long history. Writes research/long2_*.csv / .json."""
import sys, os, json, copy, time
import pandas as pd, numpy as np
from concurrent.futures import ProcessPoolExecutor
import research_long as R, backtest as B, lab as L, engine as E

HERE = R.HERE
DCA1 = dict(key='dca_dip', risk=0.02, max_pos=4, sides='long', mgmt={}, symbols=list(R.CORE))
BRK1 = dict(key='breakout_pyramid', risk=0.02, max_pos=4, sides='long', mgmt={}, symbols=list(R.CORE))


def bull(sl, keys=('ema_mom', 'ema_st', 'breakout_pyramid', 'donchian_ens')):
    out = copy.deepcopy(sl)
    for s in out:
        if s['key'] in keys: s['when'] = 'bull'
    return out


def jobs():
    J = []
    # Active alternatives (1h, 4.1 years)
    J.append(('act:DCA1H only 2%', '1h', [dict(DCA1, share=1, id='DCA1H')], {}))
    J.append(('act:DCA1H only 3%', '1h', [dict(DCA1, share=1, risk=0.03, id='DCA1H')], {}))
    J.append(('act:DCA1H only 2% maker', '1h', [dict(DCA1, share=1, id='DCA1H')], dict(entry_order='maker')))
    J.append(('act:DCA1H only 2% maker no-fallback', '1h', [dict(DCA1, share=1, id='DCA1H')], dict(entry_order='maker', maker_fallback=False)))
    J.append(('act:Active BRK bull-only', '1h', bull(R.sleeves('active')), {}))
    J.append(('act:Active BRK bull-only + maker', '1h', bull(R.sleeves('active')), dict(entry_order='maker')))
    J.append(('act:Active maker no-fallback', '1h', R.sleeves('active'), dict(entry_order='maker', maker_fallback=False)))
    J.append(('act:Active 1% risk', '1h', [dict(s, risk=0.01) for s in R.sleeves('active')], {}))
    J.append(('act:Active governor halve 15% DD', '1h', R.sleeves('active'),
              dict(governor=dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}]))))
    # bull-only on every 4h preset
    for p in ('original', 'calm', 'balanced', 'aggressive', 'boost'):
        J.append((f'bull:{p}', '4h', bull(R.sleeves(p)), {}))
        J.append((f'bull:{p} + maker', '4h', bull(R.sleeves(p)), dict(entry_order='maker')))
    # Boost: bull-only + governor
    J.append(('bull:boost + governor halve 15% DD', '4h', bull(R.sleeves('boost')),
              dict(governor=dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}]))))
    # candidate new 1h profile pieces on 4h: DCA 4h vs 1h
    J.append(('cand:Balanced bull-only + DCA1H (4h part)', '4h', bull(R.sleeves('balanced')), {}))
    return J


def mc_all(curves):
    out = {}
    for name, cv in curves.items():
        try:
            r = L.monte_carlo_daily(cv, start=float(cv.iloc[0]), n=2000, seed=1)
            out[name] = r
        except Exception as e:
            out[name] = dict(error=str(e))
    return out


def wf(name, tf, sl):
    bk = R.book(tf)
    space = [dict(path=f'sleeves[{i}].risk', values=[0.01, 0.015, 0.02, 0.03]) for i in range(min(2, len(sl)))]
    try:
        r = L.walk_forward(bk, sl, space, train_days=270, test_days=90, step_days=90, n_trials=16, seed=0, min_trades=10,
                           fund_per_bar=B.FUND_PER_BAR * (3600 if tf == '1h' else 14400) / 14400)
        return name, {k: v for k, v in r.items() if not isinstance(v, list)} | dict(windows=len(r.get('windows', [])))
    except Exception as e:
        import traceback; return name, dict(error=str(e), tb=traceback.format_exc()[-500:])


if __name__ == '__main__':
    t0 = time.time(); res = []
    with ProcessPoolExecutor(2) as ex:
        for r in ex.map(R.run_one, jobs()):
            res.append(r)
            print(f"{time.time() - t0:6.0f}s", r['name'], r['tf'], r.get('error') or f"end {r['end']} dd {r['maxdd']} cagr {r['cagr']} wm {r['worst_month']} wk {r['per_week']} yrs {r['years']}", flush=True)
    cvs = {r['name']: r.pop('_cv') for r in res if '_cv' in r}
    df = pd.DataFrame([{k: v for k, v in r.items() if k not in ('years', 'tb')} | {f'y{k}': v for k, v in (r.get('years') or {}).items()} for r in res])
    df.to_csv(f'{HERE}/research/long2_runs.csv', index=False)
    # Monte Carlo (daily blocks) on the main presets + best candidates
    base = pd.read_pickle(f'{HERE}/research/long_all_curves.pkl')
    pick = {k: v for k, v in base.items() if k.startswith('preset:') and '[' not in k}
    pick |= {k: v for k, v in cvs.items() if k in ('bull:balanced', 'bull:aggressive', 'bull:boost', 'act:DCA1H only 2%', 'act:Active BRK bull-only')}
    mc = mc_all(pick)
    json.dump(mc, open(f'{HERE}/research/long2_mc.json', 'w'), indent=1, default=float)
    for k, v in mc.items(): print('MC', k, v)
    # walk-forward (risk re-optimised each window) on the long history
    W = {}
    for name, tf, sl in (('balanced', '4h', R.sleeves('balanced')), ('aggressive', '4h', R.sleeves('aggressive')), ('bull:balanced', '4h', bull(R.sleeves('balanced'))),
                         ('active', '1h', R.sleeves('active'))):
        n, r = wf(name, tf, sl); W[n] = r; print('WF', n, {k: r[k] for k in list(r)[:14]}, flush=True)
    json.dump(W, open(f'{HERE}/research/long2_wf.json', 'w'), indent=1, default=float)
    print('done', round(time.time() - t0), 's')
