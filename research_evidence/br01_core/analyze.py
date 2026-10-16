"""Summarise BR-01 packets (scratchpad only). R per trade = $ / risk_usd (risk_usd = pnl/R, fixed-R view ~ constant);
slip_r = (SLIP/FEE) x fee_r (every fill taker at px*(1+-SLIP)); gross_before_slip - slip - fee - funding = net."""
import json, os, sys, glob, hashlib, collections
import numpy as np, pandas as pd
D = sys.argv[1]
SPF = 0.0002 / 0.0005
rows, cache_dig = {}, {}


def pf(x):
    gl = -x[x < 0].sum()
    return float(x[x > 0].sum() / gl) if gl > 0 else None


def ddr(tr, col):
    cum = tr.sort_values(['t_out', 't_in'])[col].cumsum().to_numpy()
    return float((np.maximum.accumulate(np.r_[0.0, cum]) - np.r_[0.0, cum]).max())


for f in sorted(glob.glob(f'{D}/fixr/*.json')):
    if f.endswith('.br.json'): continue
    tag = os.path.basename(f)[:-5]
    tier, rest = tag.split('_', 1)
    pk = json.load(open(f)); sc = json.load(open(f + '.br.json'))
    tr = pd.DataFrame(pk['trades']); b = pd.DataFrame(sc['trades'])
    assert len(tr) == len(b) and (tr.symbol.values == b.sym.values).all(), tag
    files = pk['cache']['files']
    lines = sorted(f"{s} {v.get('sha256', 'MISSING')}" for s, v in files.items())
    cache_dig[tier] = dict(digest=hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest(),
                           n=len(lines), incomplete=[x['symbol'] for x in pk['universe']['incomplete']],
                           run=pk['universe']['run'], regime4h=pk['cache']['regime_btc_4h'].get('sha256'), commit=pk['commit'])
    rk = (tr.pnl / tr.R.where(tr.R.abs() > 1e-12)).fillna((tr.pnl / tr.R.where(tr.R.abs() > 1e-12)).median())
    fee_r, fund_r = tr.fees / rk, tr.funding / rk
    slip_r = SPF * fee_r
    gross_bs = tr.gross / rk + slip_r
    r2 = tr.R - fee_r
    tr = tr.assign(R2=r2)
    legs = b['legs'] if 'legs' in b else pd.Series(1, index=b.index)
    hold = (pd.to_datetime(tr.t_out.str.rstrip('Z')) - pd.to_datetime(tr.t_in.str.rstrip('Z'))).dt.total_seconds() / 900
    q = pd.to_datetime(tr.t_in.str.rstrip('Z')).dt.to_period('Q').astype(str)
    cnt = sc['counts']
    n = len(tr)
    rows[tag] = dict(
        trades=n, net_r=float(tr.R.sum()), pf=pf(tr.R), win=float((tr.R > 0).mean()), dd_r=ddr(tr, 'R'),
        net_r2=float(r2.sum()), pf2=pf(r2), dd_r2=ddr(tr, 'R2'),
        gross_pt=float(gross_bs.mean()), slip_pt=float(slip_r.mean()), fee_pt=float(fee_r.mean()), fund_pt=float(fund_r.mean()),
        net_pt=float(tr.R.mean()), decomp_gap=float((gross_bs - slip_r - fee_r - fund_r - tr.R).abs().max()),
        legs_mean=float(legs.mean()), legs_dist={int(k): int(v) for k, v in legs.value_counts().sort_index().items()},
        refusals={k[7:]: v for k, v in cnt.items() if k.startswith('refuse:')},
        cancels=cnt.get('cancel', 0), off=cnt.get('off', 0), adds=cnt.get('add', 0), deadline=cnt.get('deadline', 0),
        hold_med=float(hold.median()), hold_mean=float(hold.mean()),
        why={k: int(v) for k, v in tr.why.value_counts().items()},
        quarters={k: round(float(v), 1) for k, v in tr.R.groupby(q).sum().items()},
        regime={k: [int(len(g)), round(float(g.R.sum()), 1)] for k, g in tr.groupby('regime')},
        status=pk['status'], packet=pk['packet_sha256'][:12],
        worst=float(tr.R.min()), mfe=float(tr.mfe_r.mean()), giveback=float(tr.giveback_r.mean()))
json.dump(dict(rows=rows, cache=cache_dig), open(f'{D}/summary.json', 'w'), indent=1, default=str)
print('ok', len(rows))
