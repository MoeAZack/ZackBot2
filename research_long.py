"""v3.1 extended research on the long history from the user's PC cache (core 8 coins):
4h  2021-12-19 -> 2026-10-04 (~4.8 years, includes the 2022 bear market)
1h  2022-08-26 -> 2026-10-04 (~4.1 years)
Writes research/long_*.csv and research/long_summary.json."""
import sys, os, json, copy, time, glob
import pandas as pd, numpy as np
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtest as B, engine as E, lab as L, strategies as S

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'LINKUSDT', 'AVAXUSDT']
_BOOKS = {}


def raw(tf):
    out = {}
    for f in sorted(glob.glob(f'{HERE}/data_long/{tf}/*_{tf}.csv')):
        out[os.path.basename(f).split('_')[0]] = pd.read_csv(f, parse_dates=['t'])
    return out


def book(tf):
    if tf not in _BOOKS: _BOOKS[tf] = B.Book(raw(tf))
    return _BOOKS[tf]


def btc1h():
    d = raw('1h')['BTCUSDT']; return d[['t', 'c']]


def sleeves(preset_or_list, tf=None):
    sl = E.PRESETS[preset_or_list]['sleeves'] if isinstance(preset_or_list, str) else preset_or_list
    sl = [s for s in sl if tf is None or (s.get('tf') or '4h') == tf]
    tot = sum(s['share'] for s in sl)
    out = []
    for s in sl:
        c = dict(key=s['key'], share=s['share'] / tot, risk=s['risk'], max_pos=s['max_pos'], id=s.get('id'),
                 sides=s.get('sides') or S.STRATEGIES[s['key']]['sides'], mgmt=copy.deepcopy(s.get('mgmt') or {}), symbols=list(CORE))
        for k in ('when', 'trail_entry', 'pump_guard', 'kelly', 'hours', 'vol_max_pct'):
            if s.get(k) is not None: c[k] = copy.deepcopy(s[k])
        out.append(c)
    return out


def years(cv):
    y = cv.resample('YE').last(); first = cv.iloc[0]; out = {}
    prev = first
    for ts, v in y.items(): out[str(ts.year)] = round((v / prev - 1) * 100, 1); prev = v
    return out


def summarise(tr, cv, start):
    st = B.stats(tr, cv, start=start)
    weeks = max(1e-9, (cv.index[-1] - cv.index[0]).days / 7)
    mo = cv.resample('ME').last().pct_change().dropna()
    yrs = (cv.index[-1] - cv.index[0]).days / 365.25
    cagr = ((cv.iloc[-1] / start) ** (1 / yrs) - 1) * 100 if yrs > 0 and cv.iloc[-1] > 0 else -100
    return dict(end=st['end'], ret=st['ret'], cagr=round(cagr, 1), maxdd=st['maxdd'], sharpe=st['sharpe'], trades=st['trades'],
                per_week=round(st['trades'] / weeks, 1), win=st['win'], pf=st['pf'], worst_month=st['worst_month'],
                pos_months=round((mo > 0).mean() * 100, 0), liquidations=st['liquidations'], busted=st['busted'],
                calmar=round(cagr / abs(st['maxdd']), 2) if st['maxdd'] else None,
                start_date=str(cv.index[0].date()), end_date=str(cv.index[-1].date()), years=years(cv))


def run_one(job):
    name, tf, sl, kw = job
    t = time.time()
    try:
        bk = book(tf)
        kw = dict(kw); start = kw.pop('start', 500.0)
        if kw.pop('use_btc1h', False): kw['btc1h'] = btc1h()
        tr, cv = B.run(bk, sl, start=start, fund_per_bar=B.FUND_PER_BAR * (3600 if tf == '1h' else 14400) / 14400, **kw)
        r = dict(name=name, tf=tf, **summarise(tr, cv, start), secs=round(time.time() - t, 1))
        r['_cv'] = cv.resample('1D').last().dropna()
        return r
    except Exception as e:
        import traceback; return dict(name=name, tf=tf, error=f'{e}', tb=traceback.format_exc()[-600:])


def mixed(r4, r1, start=500):
    """Side-by-side sub-accounts: half capital on each, curves added (as the app's mixed backtest does)."""
    a, b = r4['_cv'], r1['_cv']; idx = a.index.intersection(b.index)
    a = a.reindex(idx); b = b.reindex(idx)
    a0, b0 = a.iloc[0], b.iloc[0]
    cv = a / a0 * start / 2 + b / b0 * start / 2
    return cv


def jobs_presets():
    J = []
    for p in ('original', 'calm', 'balanced', 'aggressive', 'boost'):
        J.append((f'preset:{p}', '4h', sleeves(p), {}))
    J.append(('preset:active', '1h', sleeves('active'), {}))
    tl = copy.deepcopy(E.PRESETS['active']['sleeves'])
    for s in tl: s['mgmt'] = dict(s.get('mgmt') or {}, runner=dict(be_r=1, step_r=1, gap_r=1, dca_frac=.5))
    J.append(('preset:active + tight ladder', '1h', sleeves(tl), {}))
    J.append(('preset:boost_active[4h half]', '4h', sleeves('boost_active', '4h'), {}))
    J.append(('preset:boost_active[1h half]', '1h', sleeves('boost_active', '1h'), {}))
    return J


def jobs_strategies():
    J = []
    for k, meta in S.STRATEGIES.items():
        sides = meta['sides']
        for tf in ('4h', '1h'):
            J.append((f'strat:{k}', tf, [dict(key=k, share=1, risk=0.02, max_pos=4, sides=sides, mgmt={}, symbols=list(CORE), id=k)], {}))
    return J


def jobs_options():
    J = []
    for p in ('calm', 'balanced', 'aggressive'):
        base = sleeves(p)
        J.append((f'opt:{p}:baseline', '4h', base, {}))
        J.append((f'opt:{p}:maker entries', '4h', base, dict(entry_order='maker')))
        J.append((f'opt:{p}:pump guard 3ATR', '4h', base, dict(pump_guard=dict(max_candle_atr=3))))
        bull = copy.deepcopy(base)
        for s in bull:
            if s['key'] in ('ema_mom', 'ema_st'): s['when'] = 'bull'
        J.append((f'opt:{p}:trend slots bull-only', '4h', bull, {}))
        J.append((f'opt:{p}:+bear_breakdown slot', '4h', base + [dict(key='bear_breakdown', share=0.25, risk=base[0]['risk'], max_pos=4,
                  sides='short', mgmt={}, symbols=list(CORE), id='BEAR', when='bear')], {}))
        J.append((f'opt:{p}:governor halve in 15% DD', '4h', base, dict(governor=dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}]))))
        J.append((f'opt:{p}:risk rules enforce', '4h', base, dict(risk_rules=dict(
            coin_cap=dict(mode='enforce', x=3), open_risk_cap=dict(mode='enforce', pct=15),
            correlated_cap=dict(mode='enforce', n=3, rho=0.8)))))
        J.append((f'opt:{p}:BTC breaker 3%/1h (real 1h)', '4h', base, dict(use_btc1h=True,
            risk_rules=dict(btc_breaker=dict(mode='enforce', pct=3, hours=4)))))
        J.append((f'opt:{p}:TP ladder 1/2/3R', '4h', [dict(s, mgmt=dict(s['mgmt'], tps=[[1, .25], [2, .25], [3, .25]])) for s in base], {}))
        J.append((f'opt:{p}:worst-case intrabar', '4h', base, dict(pessimistic='worst')))
    J.append(('opt:boost:governor halve in 15% DD', '4h', sleeves('boost'), dict(governor=dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}]))))
    J.append(('opt:boost:governor x0.33 after +100%', '4h', sleeves('boost'), dict(governor=dict(mode='auto', rules=[{'if': 'growth_gte', 'value': 100, 'then': {'risk_mult': 0.33}, 'until': None}]))))
    J.append(('opt:active:maker entries', '1h', sleeves('active'), dict(entry_order='maker')))
    J.append(('opt:active:pump guard 3ATR', '1h', sleeves('active'), dict(pump_guard=dict(max_candle_atr=3))))
    J.append(('opt:active:risk rules enforce', '1h', sleeves('active'), dict(risk_rules=dict(
        coin_cap=dict(mode='enforce', x=3), open_risk_cap=dict(mode='enforce', pct=15), correlated_cap=dict(mode='enforce', n=3, rho=0.8)))))
    return J


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    J = []
    if which in ('all', 'presets'): J += jobs_presets()
    if which in ('all', 'strategies'): J += jobs_strategies()
    if which in ('all', 'options'): J += jobs_options()
    t0 = time.time(); res = []
    with ProcessPoolExecutor(2) as ex:
        for r in ex.map(run_one, J):
            res.append(r)
            print(f"{time.time() - t0:7.0f}s", r['name'], r['tf'], r.get('error') or f"end {r['end']} dd {r['maxdd']} cagr {r['cagr']} wk {r['per_week']} yrs {r['years']}", flush=True)
    cvs = {r['name']: r.pop('_cv') for r in res if '_cv' in r}
    if 'preset:boost_active[4h half]' in cvs and 'preset:boost_active[1h half]' in cvs:
        r4 = dict(_cv=cvs['preset:boost_active[4h half]']); r1 = dict(_cv=cvs['preset:boost_active[1h half]'])
        cv = mixed(r4, r1)
        dd = (cv / cv.cummax() - 1).min() * 100; mo = cv.resample('ME').last().pct_change().dropna()
        res.append(dict(name='preset:boost_active (mixed)', tf='4h+1h', end=round(cv.iloc[-1]), maxdd=round(dd, 1),
                        worst_month=round(mo.min() * 100, 1), years=years(cv), start_date=str(cv.index[0].date())))
        cvs['preset:boost_active (mixed)'] = cv
    os.makedirs(f'{HERE}/research', exist_ok=True)
    df = pd.DataFrame([{k: v for k, v in r.items() if k not in ('years', 'tb')} | {f'y{k}': v for k, v in (r.get('years') or {}).items()} for r in res])
    df.to_csv(f'{HERE}/research/long_{which}.csv', index=False)
    pd.to_pickle(cvs, f'{HERE}/research/long_{which}_curves.pkl')
    print('done', round(time.time() - t0), 's')
