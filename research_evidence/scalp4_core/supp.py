"""Supplement to summary.json: 2x-fee stress (fixed-R), quick_bank turnover + exit mix, exclusion list per tier."""
import json, glob, os, collections
from datetime import datetime
out = {}
for f in sorted(glob.glob('fixr/*.json')):
    p = json.load(open(f)); tag = os.path.basename(f)[:-5]; T = p['trades']
    fee_r = sum(t['fees'] / (t['pnl'] / t['R']) for t in T if t['R'] and t['pnl'])
    net = sum(t['R'] for t in T)
    gp = sum(t['R'] for t in T if t['R'] > 0); gl = -sum(t['R'] for t in T if t['R'] < 0)
    r2 = [t['R'] - (t['fees'] / (t['pnl'] / t['R']) if t['R'] and t['pnl'] else 0) for t in T]
    gp2 = sum(x for x in r2 if x > 0); gl2 = -sum(x for x in r2 if x < 0)
    o = dict(trades=len(T), net_r=round(net, 1), pf=round(gp / gl, 3) if gl else None,
             net_r_2xfee=round(sum(r2), 1), pf_2xfee=round(gp2 / gl2, 3) if gl2 else None, fee_r_per_trade=round(fee_r / len(T), 4))
    if 'quick_bank' in tag:
        days = (datetime.fromisoformat('2026-09-28') - datetime.fromisoformat('2024-10-14')).days
        bysym = collections.Counter(t['symbol'] for t in T)
        o['trades_per_coin_per_day'] = {s: round(n / days, 2) for s, n in sorted(bysym.items())}
        o['exit_mix'] = dict(collections.Counter(t['why'] for t in T))
    c = p['cache']['completeness']
    o['excluded'] = {s: dict(missing=v['missing'], first=v.get('first_missing'), last=v.get('last_missing')) for s, v in c.items() if not v['complete']}
    out[tag] = o
json.dump(out, open('supplement.json', 'w'), indent=1)
for k, v in out.items():
    print(k, v['trades'], v['net_r'], v['pf'], '2x:', v['net_r_2xfee'], v['pf_2xfee'], v.get('exit_mix', ''), list(v['excluded']))
