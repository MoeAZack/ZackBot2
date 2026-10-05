"""Regenerates the 2-year research tables shown in the Research tab with the CURRENT backtester:
  research/results_single.csv      every strategy alone (core 8 / top 40, long / both, 1% and 3% risk), 40-coin 4h data, 2 years
  research/results_timeframes.csv  every strategy on 4h vs 1h, core 8, last ~6 months (1h data limit), 1% risk
  research/results_runner.csv      winner-handling variants per preset (4h presets: 2 years; Active: 6 months of 1h)
  research/results_v31.csv         the v3.1 options on Balanced / Boost (2 years, 40 coins)
  research/results_combos.csv      via research_combos.py (452 combinations)
Data: data/ (40 coins 4h) and data1h/ (core 8 1h) - see DATA_MANIFEST.json.
Run:  python research_refresh.py [single,timeframes,runner,combos]
History: first written 2026-10-05 after the backtester's same-candle-ATR lookahead fix (trailing stops used the ATR of
the candle being walked). The winner-variant definitions below were reconstructed from the variant names and the
panel's "winners" options; the original ad-hoc scripts were not kept."""
import sys, os, glob, copy, subprocess, time
import pandas as pd
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
import load_data, backtest as B, engine as E, strategies as S

CORE = list(E.CORE8)
T6M = pd.Timestamp('2026-04-15')
_bk = {}


def book(which):
    if which not in _bk:
        if which == '4h': _bk[which] = B.Book(load_data.load())
        elif which == '4h8': _bk[which] = B.Book(load_data.load(syms=CORE))
        else: _bk[which] = B.Book({os.path.basename(f).split('_')[0]: pd.read_csv(f, parse_dates=['t']) for f in sorted(glob.glob('data1h/*_1h.csv'))})
    return _bk[which]


# ------------------------------------------------------------------ single strategies
SIDES = {k: (['short'] if v['sides'] == 'short' else ['long'] if k in ('dca_dip', 'hot_coin') else ['long', 'both']) for k, v in S.STRATEGIES.items()}


def single(job):
    key, uni, sides, risk = job
    bk = book('4h'); syms = CORE if uni == 'core8' else list(bk.syms)
    tr, cv = B.run(bk, [dict(key=key, share=1, risk=risk, max_pos=4 if uni == 'core8' else 6, sides=sides, mgmt={}, symbols=syms)])
    st = B.stats(tr, cv)
    return dict(key=key, name=S.STRATEGIES[key]['name'], style=S.STRATEGIES[key].get('style'), universe=uni if uni == 'core8' else 'top40',
                sides=sides, risk=risk, **{k: st[k] for k in ('end', 'ret', 'maxdd', 'sharpe', 'trades', 'win', 'pf', 'y1', 'y2', 'worst_month', 'busted')})


# ------------------------------------------------------------------ 4h vs 1h
def tframe(job):
    key, tf = job
    bk, fpb = (book('4h8'), B.FUND_PER_BAR) if tf == '4h' else (book('1h'), B.FUND_PER_BAR / 4)
    tr, cv = B.run(bk, [dict(key=key, share=1, risk=.01, max_pos=4, sides=S.STRATEGIES[key]['sides'], mgmt={}, symbols=CORE)], t0=T6M, fund_per_bar=fpb)
    mid = str(cv.index[0] + (cv.index[-1] - cv.index[0]) / 2)
    s = B.stats(tr, cv, split=mid); days = max(1, (cv.index[-1] - cv.index[0]).days)
    return dict(strategy=S.STRATEGIES[key]['name'], tf=tf, trades=s['trades'], per_week=round(s['trades'] / days * 7, 1), ret=s['ret'], maxdd=s['maxdd'],
                sharpe=s['sharpe'], win=s['win'], pf=s['pf'], h1=s['y1'], h2=s['y2'])


# ------------------------------------------------------------------ winner handling
LAD = dict(be_r=1, step_r=1, gap_r=1, dca_frac=1)
HOLD = dict(be_r=99, step_r=99, gap_r=99, dca_frac=1)
VARIANTS = {
    'Standard (current rules)': (None, {}),
    'Hold winners (ignore exit while trend up)': (HOLD, {}),
    'Hold winners + breakeven at 2R': (dict(HOLD, be_r=2), {}),
    'Hold + BE 2R + lock 3R behind best': (dict(HOLD, be_r=2, step_r=2, gap_r=3), {}),
    'Hold + BE 2R + give back max 50% after 4R': (dict(HOLD, be_r=2, giveback=.5, gb_from=4), {}),
    'Hold + 1/4 off at 3R + give back max 50%': (dict(HOLD, be_r=2, giveback=.5, gb_from=4), dict(tp1_r=3, tp1_frac=.25)),
    'Bank & run: 1/4 off at 3R, breakeven at 2R': (None, dict(tp1_r=3, tp1_frac=.25, be_r=2)),
    'DCA runner: half off at basket target, rest runs': (dict(HOLD, dca_frac=.5), {}),
    'Tight ladder: BE at 1R, stop locks 1R behind every new R': (LAD, {}),
    'Tight ladder + 1/3 off at 1.5R': (LAD, dict(tp1_r=1.5, tp1_frac=.33)),
    'Tight ladder + 1/2 off at 2R': (LAD, dict(tp1_r=2, tp1_frac=.5)),
    'Ladder 1.5R behind + 1/3 off at 1.5R': (dict(LAD, gap_r=1.5), dict(tp1_r=1.5, tp1_frac=.33)),
    'Tight ladder + 1/3 off + 3 ATR trail': (LAD, dict(tp1_r=1.5, tp1_frac=.33, trail_atr=3)),
    'Tight ladder + 1/3 off + 4.5 ATR trail': (LAD, dict(tp1_r=1.5, tp1_frac=.33, trail_atr=4.5)),
    'Tight ladder + 1/3 off + exit when trend breaks': (dict(LAD, trend_exit=1), dict(tp1_r=1.5, tp1_frac=.33)),
}


def runner(job):
    p, vname = job
    run, extra = VARIANTS[vname]
    one = E.PRESETS[p]['sleeves'][0]['tf'] == '1h'
    bk = book('1h') if one else book('4h')
    sl = []
    for s in E.PRESETS[p]['sleeves']:
        m = copy.deepcopy(s['mgmt']); m.update(extra)
        if run: m['runner'] = dict(run)
        sl.append(dict(key=s['key'], share=s['share'], risk=s['risk'], max_pos=s['max_pos'], sides=s['sides'], mgmt=m,
                       symbols=CORE if s['symbols'] == 'core8' else list(bk.syms)))
    tr, cv = B.run(bk, sl, start=500, t0=T6M if one else None, fund_per_bar=B.FUND_PER_BAR / 4 if one else None)
    st = B.stats(tr, cv, split=str(cv.index[0] + (cv.index[-1] - cv.index[0]) / 2))
    hold = ((tr.i_out - tr.i_in).mean() * (1 if one else 4)) if len(tr) else 0
    return dict(profile=E.PRESETS[p]['name'], variant=vname, end=st['end'], ret=st['ret'], dd=st['maxdd'], wm=st['worst_month'], sh=st['sharpe'],
                n=st['trades'], win=st['win'], avgR=round(tr.R.mean(), 2) if len(tr) else 0, maxR=round(tr.R.max(), 1) if len(tr) else 0,
                hold_h=round(hold, 0), y1=st['y1'], y2=st['y2'])


# ------------------------------------------------------------------ v3.1 feature table (Research tab "v3.1 findings")
def _preset(p, bk):
    return [dict(key=x['key'], share=x['share'], risk=x['risk'], max_pos=x['max_pos'], sides=x['sides'], mgmt=copy.deepcopy(x['mgmt']), id=x['id'],
                 symbols=CORE if x['symbols'] == 'core8' else list(bk.syms)) for x in E.PRESETS[p]['sleeves']]


GOV_DD = dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 0.5}, 'until': 'new_high'}])
V31 = [
    ('Balanced baseline', 'balanced', None, {}),
    ('TP ladder 1R/2R/3R x 25%', 'balanced', lambda sl: [dict(s, mgmt=dict(s['mgmt'], tps=[[1, .25], [2, .25], [3, .25]])) for s in sl], {}),
    ('Trailing TP from 2R, 3% pull-back', 'balanced', lambda sl: [dict(s, mgmt=dict(s['mgmt'], ttp=dict(at_r=2, dev_pct=3))) for s in sl], {}),
    ('Maker (post-only) entries', 'balanced', None, dict(entry_order='maker')),
    ('Maker entries, no market fallback', 'balanced', None, dict(entry_order='maker', maker_fallback=False)),
    ('Pump guard 3 ATR', 'balanced', None, dict(pump_guard=dict(max_candle_atr=3))),
    ('Trend slots bull-only', 'balanced', lambda sl: [dict(s, when='bull') if s['key'] in ('ema_mom', 'ema_st') else s for s in sl], {}),
    ('Risk rules enforce (coin 3x, open risk 15%, corr 3)', 'balanced', None, dict(risk_rules=dict(coin_cap=dict(mode='enforce', x=3),
        open_risk_cap=dict(mode='enforce', pct=15), correlated_cap=dict(mode='enforce', n=3, rho=0.8)))),
    ('Boost baseline', 'boost', None, {}),
    ('Governor: halve risk in a 15% drawdown', 'boost', None, dict(governor=GOV_DD)),
    ('Governor: risk x 0.33 after +100%', 'boost', None, dict(governor=dict(mode='auto', rules=[{'if': 'growth_gte', 'value': 100, 'then': {'risk_mult': 0.33}, 'until': None}]))),
    ('Martingale recovery: risk x 1.5 after a 15% drawdown', 'boost', None, dict(governor=dict(mode='auto', rules=[{'if': 'dd_gte', 'value': 15, 'then': {'risk_mult': 1.5}, 'until': 'new_high'}]))),
]


def v31(i):
    name, p, f, kw = V31[i]
    bk = book('4h'); sl = _preset(p, bk); sl = f(sl) if f else sl
    tr, cv = B.run(bk, sl, **kw)
    st = B.stats(tr, cv)
    return dict(feature=name, preset=p, end=st['end'], maxdd=st['maxdd'], worst_month=st['worst_month'], trades=st['trades'], liquidations=st['liquidations'])


def pool(fn, jobs):
    with ProcessPoolExecutor(2) as ex: return list(ex.map(fn, jobs))


if __name__ == '__main__':
    what = (sys.argv[1] if len(sys.argv) > 1 else 'single,timeframes,runner,v31,combos').split(',')
    t0 = time.time()
    if 'single' in what:
        J = [(k, u, sd, r) for k in S.STRATEGIES for u in ('core8', 'top40') for sd in SIDES[k] for r in (.01, .03)]
        pd.DataFrame(pool(single, J)).to_csv('research/results_single.csv', index=False); print('single', len(J), round(time.time() - t0), flush=True)
    if 'timeframes' in what:
        J = [(k, tf) for k in S.STRATEGIES for tf in ('4h', '1h')]
        pd.DataFrame(pool(tframe, J)).to_csv('research/results_timeframes.csv', index=False); print('timeframes', len(J), round(time.time() - t0), flush=True)
    if 'runner' in what:
        J = [(p, v) for p in ('active', 'aggressive', 'balanced', 'boost', 'calm') for v in VARIANTS
             if not (v.startswith('DCA runner') and not any(s['key'] == 'dca_dip' for s in E.PRESETS[p]['sleeves']))]
        pd.DataFrame(pool(runner, J)).to_csv('research/results_runner.csv', index=False); print('runner', len(J), round(time.time() - t0), flush=True)
    if 'v31' in what:
        pd.DataFrame(pool(v31, range(len(V31)))).to_csv('research/results_v31.csv', index=False); print('v31', round(time.time() - t0), flush=True)
    if 'combos' in what:
        subprocess.run([sys.executable, 'research_combos.py'], check=True)
        os.replace('results_combos.csv', 'research/results_combos.csv'); print('combos', round(time.time() - t0), flush=True)
