# Phase 2: one shared account. Replays candidate lots in time order under a duplicate coin/side policy.
import pickle, os, sys, json, numpy as np, pandas as pd
O = '/tmp/claude-0/work/out/'
A4 = ['exp_vwaprev','exp_range','exp_mom_short','exp_scalp','exp_structure','range_scalp','failed_breakout_scalp',
      'momentum_burst_scalp','quick_bank_runner','ema_mom','donchian_ens','squeeze_tp']        # saved sleeve order (plan 6815295243)
SCALPS = {'exp_scalp','exp_structure','range_scalp','failed_breakout_scalp','momentum_burst_scalp','quick_bank_runner'}
BOOKS = {
  'A4_all12':  dict(sleeves=A4, risk=8.0, max_pos=1),
  'A4_4h6':    dict(sleeves=[k for k in A4 if k not in SCALPS], risk=8.0, max_pos=1),
  'A1':        dict(sleeves=['exp_breakout','exp_trend','ema_st_short'], risk=16.0, max_pos=2),
  'A2_OLD3':   dict(sleeves=['bear_breakdown','ema_st_long','hot_coin'], risk=16.0, max_pos=2),
}
def load(keys, src, bp):
    out = []
    for k in keys:
        f = f'{O}{k}_{src}_{bp}.pkl'
        if not os.path.exists(f): continue
        d = pickle.load(open(f, 'rb')); d['sleeve'] = k; out.append(d)
    return pd.concat(out, ignore_index=True)
def sim(tr, order, risk, max_pos, policy, cap=None, start=4000.0, t0=None, open_cap=0.025, day_halt=0.03, min_frac=0.25):
    tr = tr if t0 is None else tr[tr.t_in >= t0]
    rank = {k: i for i, k in enumerate(order)}
    tr = tr.assign(rk=tr.sleeve.map(rank)).sort_values(['t_in', 'rk']).reset_index(drop=True)
    eq, peak, mdd, dd10 = start, start, 0.0, None
    open_ = []                    # dicts: sleeve, sym, side, t_out, risk_usd, R
    busy = {}                     # sleeve -> count open
    day, day_pnl, halted = None, 0.0, False
    taken, blocked, shrunk, stacked, maxcomb, pnl_list, Rw = 0, 0, 0, 0, 0.0, [], []
    def release(t):
        nonlocal eq, peak, mdd, dd10, day_pnl
        keep = []
        for o in sorted(open_, key=lambda o: o['t_out']):
            if o['t_out'] <= t:
                p = o['risk'] * o['R']; eq += p; day_pnl += p if o['day'] == day else 0.0
                pnl_list.append(p); busy[o['sleeve']] -= 1
                peak = max(peak, eq); dd = 1 - eq / peak; mdd = max(mdd, dd)
                if dd >= 0.10 and dd10 is None: dd10 = str(o['t_out'])[:10]
            else: keep.append(o)
        open_[:] = keep
    for r in tr.itertuples(index=False):
        release(r.t_in)
        dcairo = (r.t_in + pd.Timedelta(hours=2)).date()
        if dcairo != day: day, day_pnl, halted, day_eq = dcairo, 0.0, False, eq
        if day_pnl <= -day_halt * day_eq: halted = True
        if halted or busy.get(r.sleeve, 0) >= max_pos: continue
        same = [o for o in open_ if o['sym'] == r.sym and o['side'] == r.side]
        used = sum(o['risk'] for o in same)
        size = risk
        if same:
            if policy == 'refuse': blocked += 1; continue
            if policy == 'cap':
                rem = cap - used
                if rem < min_frac * risk: blocked += 1; continue
                if rem < risk: size = rem; shrunk += 1
            stacked += 1
        if sum(o['risk'] for o in open_) + size > open_cap * eq: continue      # account open-risk cap (all policies)
        maxcomb = max(maxcomb, used + size)
        open_.append(dict(sleeve=r.sleeve, sym=r.sym, side=r.side, t_out=r.t_out, risk=size, R=r.R, day=dcairo))
        busy[r.sleeve] = busy.get(r.sleeve, 0) + 1; taken += 1; Rw.append((size, r.R))
    release(pd.Timestamp.max)
    t_first = tr.t_in.min(); t_last = tr.t_out.max(); weeks = max(1.0, (t_last - t_first).days / 7)
    rsum = sum(s * R for s, R in Rw); rbase = sum(s for s, _ in Rw)
    return dict(pnl=round(eq - start, 1), ret_pct=round((eq / start - 1) * 100, 1), maxdd_pct=round(mdd * 100, 1), hit10=dd10,
                trades=taken, per_week=round(taken / weeks, 1), R_per_trade=round(rsum / rbase, 3) if rbase else 0.0,
                blocked=blocked, shrunk=shrunk, stacked=stacked, max_coin_side_risk=round(maxcomb, 1))
if __name__ == '__main__':
    res = []
    end = pd.Timestamp('2026-09-28')
    wins15 = {'full_2y': None, 'last12m': end - pd.DateOffset(months=12), 'last6m': end - pd.DateOffset(months=6), 'last3m': end - pd.DateOffset(months=3)}
    winsL = {'full_2021': None, 'since2024-10': pd.Timestamp('2024-10-12')}
    for book, cfg in BOOKS.items():
        for src, wins in (('15', wins15), ('long', winsL)):
            if book == 'A4_all12' and src == 'long': continue
            for bp in (7, 15, 23):
                tr = load(cfg['sleeves'], src, bp)
                pols = [('baseline_stack', 'stack', None), ('A_refuse', 'refuse', None)] + \
                       [(f'B_cap${c:g}', 'cap', c) for c in (cfg['risk'] * 1.5, cfg['risk'] * 2)]
                for wn, t0 in wins.items():
                    for pn, pol, cap in pols:
                        r = sim(tr, cfg['sleeves'], cfg['risk'], cfg['max_pos'], pol, cap, t0=t0)
                        res.append(dict(book=book, data=src, bp=bp, window=wn, policy=pn, **r))
    df = pd.DataFrame(res); df.to_csv('/tmp/claude-0/work/results.csv', index=False)
    pd.set_option('display.width', 250); pd.set_option('display.max_rows', 500)
    print(df[df.window.isin(['full_2y', 'full_2021'])].to_string(index=False))
