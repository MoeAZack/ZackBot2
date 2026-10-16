"""Research-only: one-symbol (BTCUSDT) arm-A exp_structure packet with a hand-checkable per-trade cost breakdown.

Runs the frozen runner (run_breadth_candidate.run, unchanged; default account / costs, --mgmt defaults) on --symbols
BTCUSDT for one side, through structure_regime_exit_replay.run(regime_exit=False) (= the runner exactly; parity is
tested) so the book is available, and reads each position from the runner's own read-only FeeProbe. Writes into --dir:
  trades_<side>.csv   one row per trade (prices, qty, stop, R, fees, slippage, funding, gross, net, exit reason)
  packet_<side>.json  the runner packet (+ .regime_exit.json sidecar, regime exit OFF)
and once per --dir (with --slice): btc_15m_slice.csv = the exact 15m BTCUSDT rows the book used ([warm-up, END)).

Cost model (backtest.py, frozen): market entry at the next candle open o*(1 + side*SLIP); exit fill = level*(1 - side*SLIP)
(stop / tp at the level, signal / time at the candle close); fee = FEE x notional on each fill (taker); funding =
qty * close * FUND_PER_BAR_15m on every managed candle [entry candle .. exit candle] (always a cost).
  net = side*(exit_fill - entry_fill)*qty - FEE*qty*(entry_fill + exit_fill) - funding;  R = net / risk_usd.
Dev window only, non-promotable, no edge claim. Market data outputs are never committed.

  py -3.14 tools/research/structure_btc_packet.py --cache <15m> --regime-cache <4h> --window <W> --side long --dir <out> [--slice]
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                                                                          # noqa: E402

import backtest as BT                                                                       # noqa: E402
import run_breadth_candidate as R                                                           # noqa: E402
import structure_regime_exit_replay as X                                                    # noqa: E402

KEY, SYM = 'exp_structure', 'BTCUSDT'
COLS = ['symbol', 'side', 't_in', 't_out', 'entry_open', 'entry_fill', 'exit_level', 'exit_fill', 'qty', 'stop',
        'stop_dist', 'risk_usd', 'fee_entry', 'fee_exit', 'fee_usd', 'slip_entry_usd', 'slip_exit_usd', 'slip_usd',
        'gross_before_slip', 'gross', 'funding', 'funding_recomputed', 'net', 'net_recomputed', 'R', 'why', 'bars_held']


class _Probe(R.FeeProbe):
    last = None

    def __init__(self, value):
        super().__init__(value)
        type(self).last = self


def run(*, side, out_dir, write_slice=False, write_csv=True, packet_name=None, **kw):
    """kw: runner run() arguments; default symbols=[BTCUSDT] (pass tier=... / start=... / risk=... for other views)."""
    os.makedirs(out_dir, exist_ok=True)
    if 'tier' not in kw:
        kw.setdefault('symbols', [SYM])
    old = R.FeeProbe
    R.FeeProbe = _Probe
    try:
        pkt, _ = X.run(regime_exit=False, key=KEY, side=side, mgmt_mode='defaults',
                       out=os.path.join(out_dir, packet_name or f'packet_{side}.json'), **kw)
    finally:
        R.FeeProbe = old
    book, pos = X.LAST_BOOK, _Probe.last.pos
    bt = R._ms(book.t)
    idx = {R._utc(t): j for j, t in enumerate(bt)}
    fee, slip = R.FEE_VALUE, BT.SLIP
    fpb = pkt['costs']['fund_per_bar']
    rows = []
    for t in pkt['trades']:
        i_in, i_out = idx[t['t_in']], idx[t['t_out']]
        sym = t['symbol']
        a, p = book.arr[sym], pos[(sym, i_in)]
        sd, q, avg = p['side'], p['qty'], p['avg']
        xf = avg + sd * p['gross'] / q                         # the slipped exit fill (single full close in arm A)
        xl = xf / (1 - sd * slip)
        eo = a['o'][i_in]
        fe, fx = avg * q * fee, xf * q * fee
        fund = q * fpb * float(np.sum(a['c'][i_in:i_out + 1]))
        net_h = sd * (xf - avg) * q - fe - fx - fund
        rows.append(dict(symbol=sym, side=sd, t_in=t['t_in'], t_out=t['t_out'], entry_open=eo, entry_fill=avg,
                         exit_level=xl, exit_fill=xf, qty=q, stop=p['e0'] - sd * p['R'], stop_dist=p['R'],
                         risk_usd=p['risk'], fee_entry=fe, fee_exit=fx, fee_usd=fe + fx,
                         slip_entry_usd=sd * (avg - eo) * q, slip_exit_usd=sd * (xl - xf) * q,
                         slip_usd=sd * (avg - eo) * q + sd * (xl - xf) * q, gross_before_slip=sd * (xl - eo) * q,
                         gross=p['gross'], funding=t['funding'], funding_recomputed=fund, net=t['pnl'], net_recomputed=net_h, R=t['R'],
                         why=t['why'], bars_held=i_out - i_in))
    if write_csv:
        with open(os.path.join(out_dir, f'trades_{side}.csv'), 'w', newline='') as f:
            w = csv.DictWriter(f, COLS, lineterminator='\n')
            w.writeheader()
            w.writerows(rows)
    if write_slice:
        d = book.d[SYM][['t', 'o', 'h', 'l', 'c', 'v']].copy()
        d['t'] = R._ms(d.t)
        d.insert(1, 'utc', [R._utc(x) for x in d.t])
        d.to_csv(os.path.join(out_dir, 'btc_15m_slice.csv'), index=False)
    return pkt, rows


DECOMP_TOL = 1e-6      # R; per trade the identity is exact up to float rounding


def decompose(rows) -> dict:
    """Fixed cost decomposition in R (each $ term / that trade's risk_usd):
    gross_before_slip_r - slip_r - fee_r - funding_r = net_r (asserted per trade and in total)."""
    if not rows:
        return dict(trades=0)
    r = {k: np.array([float(x[k]) / float(x['risk_usd']) for x in rows])
         for k in ('gross_before_slip', 'slip_usd', 'fee_usd', 'funding_recomputed', 'net_recomputed')}
    gap = r['gross_before_slip'] - r['slip_usd'] - r['fee_usd'] - r['funding_recomputed'] - r['net_recomputed']
    assert np.abs(gap).max() <= DECOMP_TOL, f'decomposition identity broken: {np.abs(gap).max()}'
    sides = {int(x['side']) for x in rows}
    bps = np.array([float(x['stop_dist']) / float(x['entry_fill']) * 1e4 for x in rows])
    tot = dict(gross_before_slip_r=r['gross_before_slip'].sum(), slip_r=r['slip_usd'].sum(), fee_r=r['fee_usd'].sum(),
               funding_r=r['funding_recomputed'].sum(), net_r=r['net_recomputed'].sum())
    n = len(rows)
    out = dict(trades=n, sides=sorted(sides), **{k: float(v) for k, v in tot.items()},
               per_trade={k: float(v / n) for k, v in tot.items()},
               net_r_runner=float(sum(float(x['R']) for x in rows)),
               runner_r_max_abs_diff=float(np.abs(r['net_recomputed'] - np.array([float(x['R']) for x in rows])).max()),
               identity_max_abs_gap_r=float(np.abs(gap).max()),
               median_stop_bps=float(np.median(bps)))
    assert abs(out['gross_before_slip_r'] - out['slip_r'] - out['fee_r'] - out['funding_r'] - out['net_r']) <= DECOMP_TOL * n
    return out


VIEWS = {'btcusdt_arm_a': dict(),                                   # the packet's own run (default account)
         'core10_fixed_r': dict(tier='CORE-10', start=1e9, risk=1e-6)}   # fixed-R, no-bust view (see structure_fixed_r_report)


def decomposition(out_json, out_dir, **kw) -> dict:
    """Both sides x both views; packets go to out_dir (never committed), the decomposition to out_json."""
    res = {'format': 'zb-structure-cost-decomposition/1', 'non_promotable': True, 'key': KEY, 'mgmt': 'defaults',
           'units': 'R = $ / the intended risk_usd of that trade; gross_before_slip - slip - fee - funding = net',
           'tolerance_r': DECOMP_TOL, 'views': {}}
    for view, extra in VIEWS.items():
        for side in ('long', 'short'):
            pkt, rows = run(side=side, out_dir=out_dir, write_csv=False, packet_name=f'decomp_{view}_{side}.json',
                            **dict(kw, **extra))
            d = decompose(rows)
            d['packet_sha256'] = pkt['packet_sha256']
            d['account'] = pkt['account']
            d['symbols'] = pkt['universe']['run']
            res['views'].setdefault(view, {})[side] = d
    with open(out_json, 'w', newline='') as f:
        f.write(json.dumps(res, sort_keys=True, indent=1) + '\n')
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--cache', required=True)
    ap.add_argument('--regime-cache', required=True)
    ap.add_argument('--window', required=True)
    ap.add_argument('--side', choices=('long', 'short'))
    ap.add_argument('--decompose', default=None, help='write the cost decomposition JSON here (both sides, both views)')
    ap.add_argument('--dir', required=True)
    ap.add_argument('--slice', action='store_true')
    a = ap.parse_args(argv)
    if a.decompose:
        res = decomposition(a.decompose, a.dir, cache=a.cache, regime_cache=a.regime_cache, window=a.window)
        for v, sides in res['views'].items():
            for sd, d in sides.items():
                print(v, sd, d['trades'], round(d['net_r'], 3), d['identity_max_abs_gap_r'])
        return 0
    if not a.side:
        ap.error('--side is required without --decompose')
    try:
        pkt, rows = run(side=a.side, out_dir=a.dir, write_slice=a.slice, cache=a.cache, regime_cache=a.regime_cache,
                        window=a.window)
    except R.RunnerError as e:
        print(f'REFUSED: {e}', file=sys.stderr)
        return 2
    gap = max((abs(r['net'] - r['net_recomputed']) for r in rows), default=0.0)
    print(f"{a.side}: {len(rows)} trades, R sum {pkt['stats']['aggregate']['r_sum']}, max |net - recomputed| {gap:.3e}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
