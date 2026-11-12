# Phase 1: candidate trades per sleeve, standalone, one position per coin (max_pos=9), fixed-fraction risk.
import sys, os, pickle, time, glob
sys.path.insert(0, '/home/claude/ZackBot2')
import pandas as pd, numpy as np
import backtest as B
SL4 = ['exp_vwaprev','exp_range','exp_mom_short','ema_mom','donchian_ens','squeeze_tp']
SL15 = ['exp_scalp','exp_structure','range_scalp','failed_breakout_scalp','momentum_burst_scalp','quick_bank_runner']
def raw15():
    out = {}
    for f in sorted(glob.glob('/tmp/claude-0/work/d15/*_15m.csv')):
        s = os.path.basename(f).split('_')[0]
        d = pd.read_csv(f); d['t'] = pd.to_datetime(d.t, unit='ms'); out[s] = d
    return out
def raw4(r15):
    out = {}
    for s, d in r15.items():
        g = d.set_index('t').resample('4h', label='left', closed='left', origin='epoch')
        a = pd.DataFrame(dict(o=g.o.first(), h=g.h.max(), l=g.l.min(), c=g.c.last(), v=g.v.sum(), qv=g.qv.sum())).dropna().reset_index()
        cnt = g.o.count().values; a = a[cnt[:len(a)] == 16] if len(cnt) == len(a) else a
        out[s] = a
    return out
def rawlong():
    out = {}
    for f in sorted(glob.glob('/home/claude/ZackBot2/data_long/4h/*_4h.csv')):
        s = os.path.basename(f).split('_')[0]; out[s] = pd.read_csv(f, parse_dates=['t'])
    return out
def main(key, tf, cost, sides=None, src='15'):
    if src == 'long': raw = rawlong()
    else:
        r15 = raw15(); raw = r15 if tf == '15m' else raw4(r15)
    book = B.Book(raw)
    B.FEE, B.SLIP = cost, 0.0
    t0 = time.time()
    tr, cv = B.run(book, [dict(key=key, share=1.0, risk=1e-6, max_pos=9, **({'sides': sides} if sides and sides!='-' else {}))], start=1e9, max_lev=10.0, daily_halt=1.0,
                   fund_per_bar=0.0, pessimistic=os.environ.get('PESS','path'))
    tr = pd.DataFrame(tr) if not isinstance(tr, pd.DataFrame) else tr
    T = book.t.values
    bar = pd.Timedelta('15min') if tf == '15m' else pd.Timedelta('4h')
    tr['t_in'] = pd.to_datetime(T[tr.i_in.values]); tr['t_out'] = pd.to_datetime(T[tr.i_out.values]) + bar
    tr['tf'] = tf; tr['cost'] = cost
    pickle.dump(tr[['sleeve','sym','side','t_in','t_out','R','why','tf','cost']], open(os.environ.get('OUTD','/tmp/claude-0/work/out/')+f'{key}{"_"+sides if sides and sides!="-" else ""}_{src}_{int(cost*1e4)}.pkl','wb'))
    print(key, tf, cost, len(tr), round(tr.R.sum(),1), f'{time.time()-t0:.0f}s', flush=True)
if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]), *(sys.argv[4:]))
