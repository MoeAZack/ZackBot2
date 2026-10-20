"""Fee-edge gate replay (read-only). Holdout sealed: every CSV row with candle OPEN time >= 2026-09-28T00:00Z is dropped
at text-parse time, before any DataFrame / indicator exists; a post-load assert re-checks."""
import sys, os, glob, io, json, math
REPO = r'C:\Dev\ZackBot2_feerep'
sys.path.insert(0, REPO); os.chdir(REPO)
import numpy as np, pandas as pd
import backtest as BT, strategies as S, paper_books as PB

END_MS = 1790553600000            # 2026-09-28T00:00:00Z
END_TS = pd.Timestamp('2026-09-28')
assert pd.Timestamp(END_MS, unit='ms') == END_TS
W15 = r'C:\Dev\ZackBot2_quote_pair_15m_wave2\attempt-20261018-1034-cairo\USDT'
BASES = ('BTC', 'ETH', 'SOL', 'XRP', 'BNB', 'DOGE')
COST = 0.0014                     # 2*FEE_EST(5bp) + 2*EXEC_SLIP_BPS(2bp)

def read_sealed(path, ms):
    with open(path) as f:
        head = f.readline(); keep = [head]
        for ln in f:
            t = ln.split(',', 1)[0]
            ok = (float(t) < END_MS) if ms else (t < '2026-09-28')
            if ok: keep.append(ln)
    d = pd.read_csv(io.StringIO(''.join(keep)))
    if ms: d['t'] = pd.to_datetime(d.t, unit='ms')
    else: d['t'] = pd.to_datetime(d.t)
    assert d.t.max() < END_TS, path
    return d[['t', 'o', 'h', 'l', 'c', 'v']]

raw15 = {b + 'USDT': read_sealed(os.path.join(W15, f'{b}USDT_15m.csv'), True) for b in BASES}
raw4 = {}
for p in sorted(glob.glob(os.path.join(REPO, 'data', '*_4h.csv'))):
    s = os.path.basename(p).split('_')[0]; raw4[s] = read_sealed(p, False)
raw4 = {'BTCUSDT': raw4.pop('BTCUSDT'), **raw4}
books = {'15m': BT.Book(raw15), '4h': BT.Book(raw4)}
for k, b in books.items():
    assert pd.to_datetime(b.t).max() < END_TS
    print(k, len(b.syms), 'syms', b.t.iloc[0], '->', b.t.iloc[-1], 'gaps', b.gaps, file=sys.stderr)

T0 = {'15m': np.datetime64('2024-10-14T00:00'), '4h': None}
rows = []
for key, reg in S.STRATEGIES.items():
    tf = reg.get('tf', '4h'); book = books[tf]
    sides = ['long', 'short'] if reg['sides'] == 'both' else [reg['sides']]
    for side in sides:
        cfg = [dict(key=key, share=1.0, risk=0.01, max_pos=6, id='S1', sides=side, mgmt={}, symbols=list(book.syms),
                    hours=None, vol_max_pct=None, kelly=None)]
        log = []
        warm = int((pd.to_datetime(book.t) < pd.Timestamp(T0[tf])).sum()) if T0[tf] is not None else 220
        tr, cv = BT.run(book, cfg, start=10_000.0, max_lev=50.0, daily_halt=1.0, warmup=warm,
                        t0=T0[tf], t1=np.datetime64('2026-09-28T00:00'),
                        fund_per_bar=BT.FUND_PER_BAR * (900 if tf == '15m' else 14400) / 14400, exchange_rules=None, fill_log=log)
        pos = {}
        for f in log:
            k = (f['sym'], f['pos_i']); p = pos.setdefault(k, dict(entry=None, inq=0.0, innot=0.0, outnot=0.0, outq=0.0))
            if f['kind'] == 'entry':
                p['entry'] = f; p['inq'] += f['qty']; p['innot'] += f['qty'] * f['ref']
            elif f['kind'] in ('add', 'dca'):
                p['inq'] += f['qty']; p['innot'] += f['qty'] * f['ref']
            elif f['kind'] == 'exit':
                p['outq'] += f['qty']; p['outnot'] += f['qty'] * f['ref']
        isdca = 'dca' in S.merge_mgmt(key)
        for (sym, i), p in pos.items():
            e = p['entry']
            if e is None or p['outq'] < p['inq'] * (1 - 1e-6): continue        # still open at end
            ratio = (e['risk'] / (e['qty'] * e['ref'])) if isdca else e['stop_dist'] / e['ref']
            risk_act = ratio * e['qty'] * e['ref']
            gross = e['side'] * (p['outnot'] - p['innot']) / risk_act
            net = gross - COST * p['innot'] / risk_act
            rows.append(dict(strategy=key, book=PB.book_for(key), tf=tf, side=side, stop_pct=ratio, gross=gross, net=net))
        print(key, side, len(tr), file=sys.stderr)
df = pd.DataFrame(rows)
out = r'C:\Users\princ\AppData\Local\Temp\claude\C--Users-princ-Downloads-Claude\eecc20ad-3135-487f-815e-65fc907e3498\scratchpad\feerep'
df.to_csv(os.path.join(out, 'episodes.csv'), index=False)

def st(x):
    n = len(x)
    if n == 0: return n, None, None, None
    g, m = x.gross.mean(), x.net.mean()
    t = m / (x.net.std(ddof=1) / math.sqrt(n)) if n >= 20 and x.net.std(ddof=1) > 0 else None
    return n, round(g, 3), round(m, 3), (round(t, 2) if t is not None else None)
res = []
for (b, k, s), x in df.groupby(['book', 'strategy', 'side']):
    r3, r5 = x.stop_pct <= 3 * COST, x.stop_pct <= 5 * COST
    mid, kept = x[r5 & ~r3], x[~r5]
    res.append(dict(book=b, strat=k, side=s, n=len(x), ref3=int(r3.sum()), ref5=int(r5.sum()),
                    stop_p50=round(100 * x.stop_pct.median(), 2),
                    mid=st(mid), kept5=st(kept), all=st(x)))
pd.set_option('display.width', 250); pd.set_option('display.max_rows', 100)
R = pd.DataFrame(res); print(R.to_string(index=False))
R.to_json(os.path.join(out, 'table.json'), orient='records')
