"""Sanitized SCALP-4 PAPER closed-trade rows. Reads history.json, trade_audit.jsonl, candles/BTCUSDT_4h.csv (read-only).
Emits NO order/account identifiers: no ids, qty, prices, notional, pnl $, fees $."""
import json, sys, csv, math
from datetime import datetime, timezone
import pandas as pd
base = sys.argv[1]; out = sys.argv[2]
H = [r for r in json.load(open(f'{base}/history.json', encoding='utf-8')) if str(r.get('sleeve', '')).startswith('SC-')]
aud = {}
for line in open(f'{base}/trade_audit.jsonl', encoding='utf-8'):
    try: d = json.loads(line)
    except Exception: continue
    if d.get('kind') == 'trade_audit': aud[d['id']] = d
b = pd.concat([pd.read_csv(f'{base}/candles/BTCUSDT_4h.csv'), pd.read_csv(sys.argv[3])]); b['t'] = b['t'].astype('int64')
b = b.sort_values('t').drop_duplicates('t', keep='last').reset_index(drop=True)   # fresh public klines win
assert b['t'].diff().dropna().eq(4*3600*1000).all(), 'gap in BTC 4h series'
e200 = b['c'].ewm(span=200, adjust=False).mean()          # strategies.ema = ewm(span, adjust=False)
T4 = 4 * 3600 * 1000
def ms(x):
    if isinstance(x, (int, float)): return int(x if x > 1e12 else x * 1000)
    return int(pd.Timestamp(x).tz_localize('UTC').value // 10**6 if pd.Timestamp(x).tzinfo is None else pd.Timestamp(x).value // 10**6)
def iso(m): return datetime.fromtimestamp(m / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def regime(t_ms):
    j = int(b['t'].searchsorted(t_ms - T4, side='right')) - 1   # last 4h candle with open+4h <= entry
    if j < 199: return 'n/a', None
    return ('btc_above_ema200' if b['c'][j] > e200[j] else 'btc_below_ema200'), iso(int(b['t'][j]) + T4)
rows = []
for r in sorted(H, key=lambda r: ms(r['opened'])):
    o, c = ms(r['opened']), ms(r['closed']); a = aud.get(r['id'], {})
    g, src = regime(o)
    rows.append(dict(family=r['sleeve'], strategy=r.get('strategy'), side=r['side'], regime=g, regime_src_close=src,
                     entry_time=iso(o), exit_time=iso(c), net_r=round(float(r['r']), 3),
                     mfe_r=None if a.get('peak_r') is None else round(max(0.0, float(a['peak_r'])), 3),
                     giveback_r=None if a.get('giveback_r') is None else round(float(a['giveback_r']), 3) + 0.0,
                     exit_reason=r.get('exit_reason'), audit=bool(a)))
cols = list(rows[0])
with open(out, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)
df = pd.DataFrame(rows)
def st(g):
    w = g.net_r[g.net_r > 0].sum(); l = -g.net_r[g.net_r < 0].sum()
    return dict(n=len(g), wins=int((g.net_r > 0).sum()), net_r=round(g.net_r.sum(), 3), avg_r=round(g.net_r.mean(), 3),
                pf=round(w / l, 3) if l else None, gb=round(g.giveback_r.mean(), 3) if g.giveback_r.notna().any() else None)
for k, g in df.groupby('family'): print(k, st(g))
for k, g in df.groupby(['family', 'side', 'regime']): print(k, st(g))
print('missing audit', int((~df.audit).sum()), 'mfe null', int(df.mfe_r.isna().sum()))
