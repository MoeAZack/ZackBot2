"""Summarise scalp_core packets (scratchpad only). Decomposition adapted from structure_btc_packet.decompose:
R = $ / trade risk_usd (risk_usd = pnl/R); slip_usd approximated as (SLIP/FEE) x fees (every fill taker at px*(1+-SLIP));
gross_before_slip_r - slip_r - fee_r - funding_r = net_r."""
import json, os, sys, glob, hashlib
import numpy as np, pandas as pd
D = sys.argv[1]
KEYS = ['range_scalp', 'failed_breakout_scalp', 'momentum_burst_scalp', 'quick_bank_runner']
SLIP_PER_FEE = 0.0002 / 0.0005
out = {}
def load(view, tag):
    p = f'{D}/{view}/{tag}.json'
    return json.load(open(p)) if os.path.exists(p) else None
for tier in ('CORE-10', 'CORE-20'):
    for k in KEYS:
        for s in ('long', 'short'):
            tag = f'{tier}_{k}_{s}'
            row = {}
            for view in ('acct', 'fixr'):
                pk = load(view, tag)
                if pk is None:
                    row[view] = {'missing': open(f'{D}/logs/{view}_{tag}.log').read()[-1500:] if os.path.exists(f'{D}/logs/{view}_{tag}.log') else 'no log'}
                    continue
                tr = pd.DataFrame(pk['trades'])
                a = pk['stats']['aggregate']
                r = dict(status=pk['status'], raw_sig=pk['raw_entry_signals'], trades=a['trades'], pf=a.get('pf'),
                         win=a.get('win_rate', a.get('win')), net_cash=float(tr.pnl.sum()) if len(tr) else 0.0,
                         net_r=float(tr.R.sum()) if len(tr) else 0.0, max_dd_r=a.get('max_dd_r'), max_dd_eq=a.get('max_dd_equity'),
                         bust=pk['bust']['busted'], bust_time=pk['bust']['time'], bust_trade=pk['bust']['trade_number'],
                         incomplete=[x['symbol'] for x in pk['universe']['incomplete']], run_syms=pk['universe']['run'],
                         liq=pk['stats']['liquidations'], curve_end=pk['stats']['curve_end'],
                         fee_share_abs=a.get('fees_over_abs_gross'), packet=pk['packet_sha256'][:12],
                         files={f: v.get('sha256') for f, v in pk['cache']['files'].items()},
                         regime4h=pk['cache']['regime_btc_4h'].get('sha256'), commit=pk['commit'])
                if len(tr):
                    hold = (pd.to_datetime(tr.t_out.str.rstrip('Z')) - pd.to_datetime(tr.t_in.str.rstrip('Z'))).dt.total_seconds() / 900
                    r.update(hold_med=float(hold.median()), hold_mean=float(hold.mean()),
                             mfe=float(tr.mfe_r.sum()), mae=float(tr.mae_r.sum()), giveback=float(tr.giveback_r.sum()),
                             win=float((tr.R > 0).mean()))
                    gw, gl = tr.R[tr.R > 0].sum(), -tr.R[tr.R < 0].sum()
                    r['pf_r'] = float(gw / gl) if gl > 0 else None
                    bysym = tr.groupby('symbol').R.sum()
                    r['top3_share'] = float(bysym.abs().sort_values(ascending=False).head(3).sum() / bysym.abs().sum()) if bysym.abs().sum() else None
                    r['top3'] = {k2: round(v, 1) for k2, v in bysym.reindex(bysym.abs().sort_values(ascending=False).index[:3]).items()}
                    nz = tr[tr.R.abs() > 1e-12]
                    risk = (nz.pnl / nz.R)
                    rk = float(risk.median())  # fixed-R: risk_usd ~ constant; per-trade risk used where defined
                    rr = tr.pnl / tr.R.where(tr.R.abs() > 1e-12)
                    rr = rr.fillna(rk)
                    fee_r = tr.fees / rr; slip_r = SLIP_PER_FEE * fee_r; fund_r = tr.funding / rr
                    gross_bs = tr.gross / rr + slip_r
                    net_r = tr.pnl / rr
                    gap = float((gross_bs - slip_r - fee_r - fund_r - net_r).abs().max())
                    r['decomp'] = dict(gross_before_slip_r=float(gross_bs.sum()), slip_r=float(slip_r.sum()), fee_r=float(fee_r.sum()),
                                       funding_r=float(fund_r.sum()), net_r=float(net_r.sum()), gap=gap,
                                       runner_r_diff=float((net_r - tr.R).abs().max()))
                    n = len(tr)
                    r['gross_pt'] = r['decomp']['gross_before_slip_r'] / n
                    r['cost_pt'] = (r['decomp']['slip_r'] + r['decomp']['fee_r'] + r['decomp']['funding_r']) / n
                    r['fee_r_pt'] = r['decomp']['fee_r'] / n
                    q = pd.to_datetime(tr.t_in.str.rstrip('Z')).dt.to_period('Q').astype(str)
                    r['quarters'] = {k2: round(float(v), 2) for k2, v in tr.R.groupby(q).sum().items()}
                    r['regime'] = {k2: [int(len(g)), round(float(g.R.sum()), 2)] for k2, g in tr.groupby('regime')}
                row[view] = r
            out[tag] = row
json.dump(out, open(f'{D}/summary.json', 'w'), indent=1, default=str)
print('ok', len(out))
