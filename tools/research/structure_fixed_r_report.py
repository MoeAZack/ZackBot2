"""Research-only post-processor: fixed-R (no-bust) view of a run_breadth_candidate packet. Dev window only, no edge claim.

The fixed-R packet itself is the frozen runner, unchanged, with its own account arguments set so equity never matters:
--start 1e9 --risk 1e-6 (risk $1,000 per trade, total drift << 1% of equity): the leverage cap, the notional floor, the
8% daily halt and the 2%-of-start loop break can never bind, so the trade set is the strategy's own (max_pos still
applies) and every trade's R = realised / intended risk is the 1R-normalised result. Costs (fee, slippage, funding)
are proportional, so R does not depend on size.

This script only reads packets: totals, BTC-regime split, calendar-quarter split (by entry time), longest losing run
(exit order), and the exactness check against a reference packet (the default-account run): on trades with the same
(symbol, entry, exit), the per-trade R must agree unless the reference trade was leverage-capped or partially sized.

  py -3.14 tools/research/structure_fixed_r_report.py <fixed packet> [--ref <default packet>] [--json out]
"""
import argparse
import json
import sys

import numpy as np
import pandas as pd


def frame(pkt) -> pd.DataFrame:
    tr = pd.DataFrame(pkt['trades'])
    if not len(tr):
        return tr
    tr['t_in'] = pd.to_datetime(tr.t_in.str.rstrip('Z'))
    tr['t_out'] = pd.to_datetime(tr.t_out.str.rstrip('Z'))
    return tr.sort_values(['t_out', 't_in', 'symbol']).reset_index(drop=True)


def totals(tr) -> dict:
    if not len(tr):
        return dict(trades=0, net_r=0.0, pf=None, max_dd_r=0.0, win_pct=None)
    r = tr.R.to_numpy(float)
    cum = np.r_[0.0, np.cumsum(r)]
    gw, gl = r[r > 0].sum(), -r[r < 0].sum()
    return dict(trades=int(len(r)), net_r=float(r.sum()), pf=float(gw / gl) if gl > 0 else None,
                max_dd_r=float((np.maximum.accumulate(cum) - cum).max()), win_pct=float((r > 0).mean() * 100))


def losing_run(tr) -> dict:
    """Longest run of consecutive losing trades (R < 0) in exit order: length, R sum, first exit, last exit."""
    best, cur, start = dict(n=0, r=0.0, first=None, last=None), 0, 0
    r = tr.R.to_numpy(float) if len(tr) else np.zeros(0)
    for k, x in enumerate(r):
        if x < 0:
            if cur == 0:
                start = k
            cur += 1
            if cur > best['n']:
                best = dict(n=cur, r=float(r[start:k + 1].sum()), first=str(tr.t_out[start]), last=str(tr.t_out[k]))
        else:
            cur = 0
    return best


def by(tr, col) -> dict:
    return {str(k): dict(trades=int(len(g)), net_r=float(g.R.sum())) for k, g in tr.groupby(col, sort=True)}


def quarters(tr) -> dict:
    q = tr.t_in.dt.to_period('Q').astype(str) if len(tr) else pd.Series([], dtype=str)
    return by(tr.assign(q=q), 'q')


def parity(tr, ref) -> dict:
    """Same (symbol, t_in, t_out) in both packets: R agreement (abs diff)."""
    k = ['symbol', 't_in', 't_out']
    m = tr.merge(ref, on=k, suffixes=('', '_ref'))
    d = (m.R - m.R_ref).abs()
    return dict(common=int(len(m)), ref_trades=int(len(ref)), exact_1e9=int((d <= 1e-9).sum()),
                max_abs_diff=float(d.max()) if len(d) else None,
                diff_trades=[dict(symbol=r.symbol, t_in=str(r.t_in), R=r.R, R_ref=r.R_ref)
                             for r in m[d > 1e-9].itertuples()][:20])


def report(pkt, ref=None) -> dict:
    tr = frame(pkt)
    out = dict(source_packet=pkt['packet_sha256'], account=pkt['account'], side=pkt['side'],
               bust=pkt['bust']['busted'], totals=totals(tr), regime=by(tr, 'regime') if len(tr) else {},
               quarters=quarters(tr), longest_losing_run=losing_run(tr))
    out['positive_buckets'] = sorted([f'regime:{k}' for k, v in out['regime'].items() if v['net_r'] > 0] +
                                     [f'quarter:{k}' for k, v in out['quarters'].items() if v['net_r'] > 0])
    if ref is not None:
        out['parity_vs_ref'] = parity(tr, frame(ref))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('packet')
    ap.add_argument('--ref', default=None)
    ap.add_argument('--json', default=None)
    a = ap.parse_args(argv)
    with open(a.packet) as f:
        pkt = json.load(f)
    ref = None
    if a.ref:
        with open(a.ref) as f:
            ref = json.load(f)
    out = report(pkt, ref)
    txt = json.dumps(out, indent=1, default=str)
    if a.json:
        with open(a.json, 'w') as f:
            f.write(txt)
    print(txt)
    return 0


if __name__ == '__main__':
    sys.exit(main())
