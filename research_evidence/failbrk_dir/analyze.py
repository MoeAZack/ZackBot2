"""Summarise the failbrk_dir packets: arm A = none, arm B = counter (refuse counter-regime entries).
Usage: py -3.14 analyze.py <packet dir with acct/ fixr/ logs/> <out summary.json> [<scalp4_core baseline summary.json>]
Decomposition as scalp4_core/analyze.py: R = $ / trade risk_usd (risk_usd = pnl / R); slip_usd ~ (SLIP / FEE) x fees
(every fill taker at px * (1 +- SLIP)); gross_before_slip_r - slip_r - fee_r - funding_r = net_r."""
import json
import os
import sys

import pandas as pd

D, OUT = sys.argv[1], sys.argv[2]
BASE = sys.argv[3] if len(sys.argv) > 3 else None
SLIP_PER_FEE = 0.0002 / 0.0005
TIERS, SIDES, VIEWS, ARMS = ('CORE-10', 'CORE-20'), ('long', 'short'), ('acct', 'fixr'), ('none', 'counter')


def load(view, tag):
    p = f'{D}/{view}/{tag}.json'
    return json.load(open(p)) if os.path.exists(p) else None


def quarters(tr):
    if not len(tr):
        return {}
    q = pd.to_datetime(tr.t_in.str.rstrip('Z')).dt.to_period('Q').astype(str)
    return {k: round(float(v), 4) for k, v in tr.R.groupby(q).sum().items()}


def summarise(pk):
    tr = pd.DataFrame(pk['trades'])
    a = pk['stats']['aggregate']
    r = dict(status=pk['status'], raw_entry_signals=pk['raw_entry_signals'], n=a['trades'], wins=a['wins'],
             net_r=float(tr.R.sum()) if len(tr) else 0.0, avg_r=a['r_mean'], pf=a['pf'], max_dd_r=a['max_dd_r'],
             giveback_r_mean=a['giveback_r_mean'], net_cash=a['pnl'], bust=pk['bust']['busted'],
             bust_time=pk['bust']['time'], commit=pk['commit'], packet=pk['packet_sha256'][:12],
             incomplete=[x['symbol'] for x in pk['universe']['incomplete']],
             refused=(pk.get('regime_refuse') or {}).get('refused_signals', 0),
             regime_refuse=(pk.get('config') or {}).get('regime_refuse', 'none'))
    if len(tr):
        gw, gl = tr.R[tr.R > 0].sum(), -tr.R[tr.R < 0].sum()
        r['pf_r'] = float(gw / gl) if gl > 0 else None
        nz = tr[tr.R.abs() > 1e-12]
        rk = float((nz.pnl / nz.R).median())
        rr = (tr.pnl / tr.R.where(tr.R.abs() > 1e-12)).fillna(rk)
        fee_r = tr.fees / rr
        slip_r = SLIP_PER_FEE * fee_r
        fund_r = tr.funding / rr
        gross_bs = tr.gross / rr + slip_r
        net = tr.pnl / rr
        r['decomp'] = dict(gross_before_slip_r=float(gross_bs.sum()), slip_r=float(slip_r.sum()), fee_r=float(fee_r.sum()),
                           funding_r=float(fund_r.sum()), net_r=float(net.sum()),
                           gap=float((gross_bs - slip_r - fee_r - fund_r - net).abs().max()))
        r['quarters'] = quarters(tr)
        r['regime'] = {k: [int(len(g)), round(float(g.R.sum()), 3)] for k, g in tr.groupby('regime')}
    return r, tr


out = {'spec': 'https://github.com/MoeAZack/ZackBot2/issues/13#issuecomment-6228353544',
       'key': 'failed_breakout_scalp', 'mgmt': 'defaults',
       'window': '2024-10-14T00:00:00Z/2026-09-28T00:00:00Z (development window only)',
       'arms': {'A': 'none', 'B': 'counter: refuse LONG under btc_below_ema200, SHORT under btc_above_ema200'},
       'views': {'acct': 'app account defaults (compounding, can bust)', 'fixr': '--start 1e9 --risk 1e-6 (fixed risk)'},
       'runs': {}, 'compare': {}, 'baseline_check': {}, 'decision': {}}
base = json.load(open(BASE)) if BASE else None
for tier in TIERS:
    for side in SIDES:
        for view in VIEWS:
            arm = {}
            for a in ARMS:
                tag = f'{tier}_{side}_{a}'
                pk = load(view, tag)
                if pk is None:
                    log = f'{D}/logs/{view}_{tag}.log'
                    out['runs'][f'{view}/{tag}'] = {'missing': open(log).read()[-800:] if os.path.exists(log) else 'no log'}
                    continue
                arm[a] = summarise(pk)
                out['runs'][f'{view}/{tag}'] = arm[a][0]
            if len(arm) < 2:
                continue
            (A, ta), (Bm, tb) = arm['none'], arm['counter']
            ka = set(zip(ta.symbol, ta.t_in)) if len(ta) else set()
            kb = set(zip(tb.symbol, tb.t_in)) if len(tb) else set()
            qa, qb = A.get('quarters', {}), Bm.get('quarters', {})
            qs = sorted(set(qa) | set(qb))
            dq = {q: round(qb.get(q, 0.0) - qa.get(q, 0.0), 4) for q in qs}
            out['compare'][f'{view}/{tier}_{side}'] = dict(
                d_n=Bm['n'] - A['n'], d_net_r=Bm['net_r'] - A['net_r'],
                d_pf=(Bm['pf'] - A['pf']) if Bm['pf'] is not None and A['pf'] is not None else None,
                d_max_dd_r=(Bm['max_dd_r'] or 0) - (A['max_dd_r'] or 0),
                refused=Bm['refused'], substitute_entries=len(kb - ka), dropped_entries=len(ka - kb),
                common_entries=len(ka & kb), quarters_delta=dq,
                quarters_b_better=sum(v > 0 for v in dq.values()), quarters=len(dq))
            if base is not None:
                b = base.get(f'{tier}_failed_breakout_scalp_{side}', {}).get(view)
                if b:
                    same = (b['trades'] == A['n'] and abs(b['net_r'] - A['net_r']) < 1e-6
                            and (b['pf'] == A['pf'] or abs((b['pf'] or 0) - (A['pf'] or 0)) < 1e-9))
                    out['baseline_check'][f'{view}/{tier}_{side}'] = dict(
                        match=bool(same), baseline=dict(n=b['trades'], net_r=b['net_r'], pf=b['pf'], commit=b['commit']),
                        arm_none=dict(n=A['n'], net_r=A['net_r'], pf=A['pf'], commit=A['commit']))

# fixed decision rule, per side, primary view fixr (acct reported too)
for view in VIEWS:
    for side in SIDES:
        c = [out['compare'].get(f'{view}/{t}_{side}') for t in TIERS]
        r = [(out['runs'].get(f'{view}/{t}_{side}_none'), out['runs'].get(f'{view}/{t}_{side}_counter')) for t in TIERS]
        if any(x is None for x in c):
            out['decision'][f'{view}/{side}'] = 'incomplete'
            continue
        net = all(x['d_net_r'] > 0 for x in c)
        pf = all(b['pf'] is not None and a['pf'] is not None and b['pf'] >= a['pf'] for a, b in r)
        qtr = all(x['quarters_b_better'] * 3 >= 2 * x['quarters'] for x in c)
        out['decision'][f'{view}/{side}'] = dict(
            net_r_b_gt_a_both_tiers=net, pf_b_ge_a_both_tiers=pf, b_better_in_2_3_quarters_both_tiers=qtr,
            verdict='B improves' if (net and pf and qtr) else 'no change')
out['decision']['rule'] = ('"B improves" only if net R B > A on BOTH tiers AND PF_B >= PF_A on both tiers AND B - A '
                           'quarterly net R is > 0 in >= 2/3 of quarters on both tiers; else "no change". Evaluated per '
                           'side; the fixr view is primary (acct compounds and busts).')
json.dump(out, open(OUT, 'w'), indent=1, default=str)
for k, v in out['decision'].items():
    print(k, v if isinstance(v, str) else v['verdict'])
