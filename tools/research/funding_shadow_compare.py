"""B4 `funding_context_shadow` research comparison (development window only; SHADOW research, no edge claim).

Reads existing breadth-runner packets (format zb-breadth-candidate/1: trades with symbol, side, t_in, R, pnl, fees,
regime) plus the collector's funding history, tags every trade with `funding_context.funding_ctx` at t_in (the open of
the fill bar = the decision candle's close), and reports the baseline against the ONE prespecified counterfactual
filter `crowd == +1 and z30 >= 2` (contract B4; no threshold search, no other filter).

INCOMPLETE trades are never dropped (the filter cannot see them); their share is reported. The frozen runner is not
touched. Funding rows at/after the sealed cutoff are never read (`funding_context.load_funding_csv` stops there), and
a packet whose window or trades reach the cutoff is refused.

Interval source (`--interval-source`):
  derived       (default) point-in-time interval log from the sealed settlement spacing.
  funding_info  the collector's funding_info/ALL.csv; rows first seen at/after the cutoff are dropped at load, so with
                today's collector (a single 2026-10-14 snapshot) every trade is INCOMPLETE (no_interval_in_force).

Fee R per trade = fees / (pnl / R) (as scalp4 supp.py); the 2x-fee row subtracts one more fee R from each trade.

  py -3.14 tools/research/funding_shadow_compare.py --packets "<dir>/*.json" --out <dir> [--funding-dir <dir>]
"""
import argparse
import csv
import glob
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import funding_context as FC  # noqa: E402

SEALED_CUTOFF = datetime(2026, 9, 28, tzinfo=timezone.utc)
FORMAT = 'zb-funding-shadow-compare/1'


def default_funding_dir():
    return os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'ZackBot', 'market_data')


def _iso(s):
    return datetime.fromisoformat(str(s).replace('Z', '+00:00'))


def fee_r(t):
    R, pnl, fees = t.get('R'), t.get('pnl'), t.get('fees')
    if not R or not pnl or fees is None:
        return 0.0
    return float(fees) / (float(pnl) / float(R))


def stats(trades):
    R = [float(t['R']) for t in trades]
    r2 = [float(t['R']) - fee_r(t) for t in trades]
    def pf(xs):
        gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
        return round(gp / gl, 4) if gl > 0 else None
    return dict(n=len(R), net_r=round(sum(R), 4), pf=pf(R), net_r_2xfee=round(sum(r2), 4), pf_2xfee=pf(r2))


def quarter(t_in):
    d = _iso(t_in)
    return f'{d.year}Q{(d.month - 1) // 3 + 1}'


class FundingSource:
    """Per-symbol sealed history + interval log, loaded lazily once."""
    def __init__(self, funding_dir, cutoff, interval_source='derived'):
        if interval_source not in ('derived', 'funding_info'):
            raise ValueError('interval_source must be derived or funding_info')
        self.dir, self.cutoff, self.src = funding_dir, cutoff, interval_source
        self._h, self._log, self._info_rows = {}, {}, None

    def _info(self):
        if self._info_rows is None:
            rows, cut = [], FC.utc_ms(self.cutoff)
            p = os.path.join(self.dir, 'funding_info', 'ALL.csv')
            if os.path.exists(p):
                with open(p, encoding='utf-8', newline='') as f:
                    for r in csv.DictReader(f):
                        fs = (r.get('first_seen_utc') or '').strip()
                        if fs and FC.utc_ms(_iso(fs)) < cut:          # sealed: post-cutoff observations never used
                            rows.append(r)
            self._info_rows = rows
        return self._info_rows

    def get(self, sym):
        if sym not in self._h:
            h = FC.load_funding_csv(os.path.join(self.dir, 'funding_rate', f'{sym}.csv'), self.cutoff)
            self._h[sym] = h
            if h is None or not h.times:
                self._log[sym] = None
            elif self.src == 'derived':
                self._log[sym] = FC.derive_interval_log(h) or None
            else:
                self._log[sym] = FC.interval_log_from_funding_info(self._info(), sym) or None
        return self._h[sym], self._log[sym]


def tag_trades(trades, source, cutoff):
    out = []
    for t in trades:
        ti = _iso(t['t_in'])
        if ti >= cutoff:
            raise ValueError(f"trade t_in {t['t_in']} at/after the sealed cutoff; refused")
        h, log = source.get(t['symbol'])
        ctx = FC.funding_ctx(h, log, ti, int(t['side']))
        out.append((t, ctx, FC.counterfactual_drop(ctx)))
    return out


def compare(trades, source, cutoff=SEALED_CUTOFF):
    tagged = tag_trades(trades, source, cutoff)
    base = [t for t, _, _ in tagged]
    removed = [t for t, _, d in tagged if d]
    kept = [t for t, _, d in tagged if not d]
    inc = [c for _, c, _ in tagged if c['status'] != 'OK']
    def split(keyf):
        g = defaultdict(lambda: ([], [], []))
        for t, _, d in tagged:
            k = keyf(t)
            if k is None: continue
            g[k][0].append(t); (g[k][1] if d else g[k][2]).append(t)
        return {k: dict(baseline=stats(b), removed=stats(r), filtered=stats(f)) for k, (b, r, f) in sorted(g.items())}
    res = dict(baseline=stats(base), removed=stats(removed), filtered=stats(kept),
               incomplete=dict(n=len(inc), share=round(len(inc) / len(base), 4) if base else None,
                               reasons=dict(Counter(r for c in inc for r in c['reasons']))),
               ok_crowd=dict(Counter(c['crowd'] for _, c, _ in tagged if c['status'] == 'OK')),
               by_quarter=split(lambda t: quarter(t['t_in'])),
               by_regime=split(lambda t: t.get('regime')) if any(t.get('regime') for t in base) else None)
    return res, tagged


def packet_label(path, p):
    name = os.path.basename(path)[:-5]
    tier = name.split('_')[0] if name.startswith('CORE-') else f"book{len(p.get('universe', {}).get('book', []))}"
    mode = ('profit_capture' if name.endswith('profit_capture') else 'defaults' if name.endswith('defaults') else
            p.get('strategy', {}).get('mgmt_mode', ''))
    return dict(name=name, key=p.get('strategy', {}).get('key'), side=p.get('side'), tier=tier, mode=mode)


def run(packet_globs, out_dir, funding_dir=None, interval_source='derived', cutoff=SEALED_CUTOFF):
    if cutoff > SEALED_CUTOFF:
        raise ValueError('cutoff after the sealed holdout start; refused')
    src = FundingSource(funding_dir or default_funding_dir(), cutoff, interval_source)
    files = sorted({f for g in packet_globs for f in glob.glob(g)})
    if not files:
        raise SystemExit('no packets matched')
    rows = []
    for f in files:
        with open(f, encoding='utf-8') as fh:
            p = json.load(fh)
        w = p.get('window', {})
        if w.get('end') and _iso(w['end']) > cutoff:
            raise ValueError(f'{f}: window ends after the sealed cutoff; refused')
        res, _ = compare(p['trades'], src, cutoff)
        rows.append(dict(packet=packet_label(f, p), packet_sha256=p.get('packet_sha256'), **res))
    doc = dict(format=FORMAT, cutoff=cutoff.strftime('%Y-%m-%dT%H:%M:%SZ'), interval_source=interval_source,
               counterfactual='drop entries with crowd == +1 and z30 >= 2 (prespecified, no search)',
               evidence='development window only; shadow research; not promotable', results=rows)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'funding_shadow_compare.json'), 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
    with open(os.path.join(out_dir, 'funding_shadow_compare.txt'), 'w', encoding='utf-8') as fh:
        fh.write(table(rows))
    return doc


def _f(x, nd=2):
    return '-' if x is None else (f'{x:+.{nd}f}' if isinstance(x, float) and nd == 1 else f'{x:.{nd}f}' if isinstance(x, float) else str(x))


def table(rows):
    hdr = ('packet', 'n', 'baseR', 'basePF', 'rm_n', 'rm_R', 'rm_PF', 'filtR', 'filtPF', 'filtPF2x', 'INC%')
    lines = ['  '.join(hdr)]
    for r in rows:
        b, m, f, i = r['baseline'], r['removed'], r['filtered'], r['incomplete']
        lines.append('  '.join(str(x) for x in (
            r['packet']['name'], b['n'], f"{b['net_r']:+.1f}", _f(b['pf'], 3), m['n'], f"{m['net_r']:+.1f}", _f(m['pf'], 3),
            f"{f['net_r']:+.1f}", _f(f['pf'], 3), _f(f['pf_2xfee'], 3), f"{100 * (i['share'] or 0):.1f}")))
    return '\n'.join(lines) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--packets', action='append', required=True, help='glob of runner packet JSON files (repeatable)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--funding-dir', default=None, help='market_data root (default %%LOCALAPPDATA%%\\ZackBot\\market_data)')
    ap.add_argument('--interval-source', default='derived', choices=('derived', 'funding_info'))
    a = ap.parse_args(argv)
    doc = run(a.packets, a.out, a.funding_dir, a.interval_source)
    sys.stdout.write(table(doc['results']))


if __name__ == '__main__':
    main()
