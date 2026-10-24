"""INTRABAR-DISCLOSURE-01 (Codex #206 comment 6388342400): evidence-only, price-free disclosure of a scalp-exec cell's
unresolved intrabar segments.

The v1 packet aggregates intrabar labels; this re-runs ONLY the cell's reference replay (costs off, the replay whose
labels the packet counts) on the same frozen inputs and reports, per symbol, every unresolved segment: its index in the
label log, segment_open_ms, minute_open_ms (the 1m candle spanning both a stop and a target; None for NO-1M), label,
side held, and whether a closed trade's exit fill falls inside that 15m candle (with the exit kinds). Nothing here
changes a decision, cost or fill: scalp_exec_15m.bar_path is wrapped for the run only and returns its own result
unchanged, and the run refuses unless its aggregate labels and trade count equal the packet's when one is given.
The output carries no price, quantity or P&L - only ms timestamps, labels, sides, counts and shares.
"""
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scalp_exec_15m as X                                                                    # noqa: E402

FORMAT = 'intrabar-disclosure/1'
SIDE = {1: 'LONG', -1: 'SHORT'}


class DisclosureError(ValueError):
    pass


def _side(sides) -> str:
    return SIDE[next(iter(sides))] if len(sides) == 1 else 'BOTH'


def _spanning_minute(bar, minutes, levels, lo, hi):
    """open_ms of the first 1m candle of minutes [lo, hi) that spans a stop and a target (the SAME-MINUTE case)."""
    stops, tg = levels
    for m in (X.complete_minutes(bar, minutes) or [])[lo:hi]:
        if X._spans(m, stops) and X._spans(m, tg):
            return m.open_ms
    raise DisclosureError(f'SAME-MINUTE segment at {bar.open_ms} has no spanning minute')


def reference_segments(run, minutes, sleeves, t0, *, start, rules, settings, latency_s):
    """The reference replay exactly as run_cell runs it, plus one context record per bar_path call (in call order)."""
    ctx, real = [], X.bar_path

    def spy(bar, mins, sides, levels, lo=0, hi=15):
        pts, label = real(bar, mins, sides, levels, lo, hi)
        ctx.append(dict(open_ms=bar.open_ms, label=label, side=_side(sides),
                        minute_open_ms=_spanning_minute(bar, mins, levels, lo, hi) if label == X.SAME_MINUTE else None))
        return pts, label
    X.bar_path = spy
    try:
        out, fills, labels = X._engine(run, minutes, sleeves, t0, start=start, rules=rules, settings=settings,
                                       latency_s=latency_s)
    finally:
        X.bar_path = real
    return out, fills, labels, ctx


def _pair(labels, ctx):
    """Label-log entries with their bar_path context. NO-1M-NO-LATENCY entries come from no bar_path call."""
    it, recs = iter(ctx), []
    for k, (s, open_ms, label) in enumerate(labels):
        c = None if label == X.NO_LATENCY else next(it, None)
        if label != X.NO_LATENCY and (c is None or (c['open_ms'], c['label']) != (open_ms, label)):
            raise DisclosureError(f'label log entry {k} does not match its bar_path call')
        recs.append((k, s, open_ms, label, c))
    if next(it, None) is not None:
        raise DisclosureError('bar_path calls without a label log entry')
    return recs


def disclose(raw15, minutes, sleeves, t0, *, funding=None, exchange_rules=None, start=2000.0, latency_s=0,
             settings=None, packet=None) -> dict:
    X.check_sleeves(sleeves)
    bad = X.check_inputs(raw15, minutes, funding)
    run = {s: df for s, df in raw15.items() if s not in bad}
    if not run:
        raise DisclosureError('no complete symbol to run')
    out, fills, labels, ctx = reference_segments(run, minutes, sleeves, t0, start=start, rules=exchange_rules,
                                                 settings=settings, latency_s=latency_s)
    trades = X.trades_of(fills, out['history'], exchange_rules or {})
    exits = {}
    for tr in trades:
        for f in tr['closes']:
            exits.setdefault(tr['sym'], []).append((int(f['t_ms']), f['kind']))
    lab = [x[2] for x in labels]
    by_label = {k: lab.count(k) for k in (X.ONE_LEVEL, X.RES_1M, X.SAME_MINUTE, X.NO_1M, X.TWO_SIDED, X.NO_LATENCY)}
    unres = sum(x in X.UNRESOLVED for x in lab)
    if packet is not None:
        ib = packet['intrabar']
        got = dict(segments=len(lab), by_label=by_label, unresolved=unres, trades=len(trades))
        want = dict(segments=ib['segments'], by_label=ib['by_label'], unresolved=ib['unresolved'],
                    trades=len(packet['repriced']['trades']))
        if got != want:
            raise DisclosureError(f'reference replay does not reproduce the packet: {got} != {want}')
    per = {s: dict(trades=sum(t['sym'] == s for t in trades), unresolved=0, segments=[]) for s in run}
    for k, s, open_ms, label, c in _pair(labels, ctx):
        if label not in X.UNRESOLVED:
            continue
        kinds = sorted(kind for t, kind in exits.get(s, ()) if open_ms <= t < open_ms + X.TF_MS)
        per[s]['unresolved'] += 1
        per[s]['segments'].append(dict(index=k, segment_open_ms=open_ms, minute_open_ms=c['minute_open_ms'],
                                       label=label, side=c['side'], overlaps_closed_trade_exit=bool(kinds),
                                       exit_kinds=kinds))
    for d in per.values():
        d['unresolved_share'] = d['unresolved'] / d['trades'] if d['trades'] else 0.0
    return dict(format=FORMAT, evidence_only=True, latency_s=latency_s, excluded_symbols=bad, segments=len(lab),
                by_label=by_label, unresolved=unres, trades=len(trades),
                unresolved_share=unres / len(trades) if trades else 0.0, symbols=per)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='INTRABAR-DISCLOSURE-01: price-free unresolved-segment disclosure')
    for k in ('--config', '--manifest', '--store', '--static', '--out'):
        ap.add_argument(k, required=True)
    ap.add_argument('--symbols', required=True)
    ap.add_argument('--window', required=True)
    ap.add_argument('--key', required=True, choices=X.KEYS)
    ap.add_argument('--side', required=True, choices=('long', 'short', 'both'))
    ap.add_argument('--latency-s', type=int, required=True, choices=X.LATENCIES)
    ap.add_argument('--rules')
    ap.add_argument('--start', type=float, default=2000.0)
    ap.add_argument('--packet', help='the cell packet to cross-check (its intrabar counts and trade count only)')
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        raise DisclosureError(f'{a.out} exists (never overwritten)')
    lo, hi = (int(pd.Timestamp(x).value // 10 ** 6) for x in a.window.split('/'))
    doc = X.load_config(a.config)
    syms = a.symbols.split(',')
    data = X.load_store(a.manifest, a.store, a.static, syms, lo, hi)
    rules = packet = None
    if a.rules:
        with open(a.rules, encoding='utf-8') as fh:
            rules = json.load(fh)
    if a.packet:
        with open(a.packet, encoding='utf-8') as fh:
            pk = json.load(fh)
        packet = dict(intrabar=pk['intrabar'], repriced=dict(trades=pk['repriced']['trades']))
    raw15 = data['raw15']
    res = disclose(raw15, data['minutes'], X.cell_sleeves(doc, a.key, a.side), X.window_start(raw15, lo),
                   funding=data['funding'], exchange_rules=rules, start=a.start, latency_s=a.latency_s,
                   settings={'PRESET': doc['preset']}, packet=packet)
    res.update(key=a.key, side=a.side, window=a.window, packet_checked=bool(a.packet))
    with open(a.out, 'x', encoding='utf-8') as fh:
        json.dump(res, fh, sort_keys=True)
    print(json.dumps(dict(out=a.out, unresolved=res['unresolved'], trades=res['trades']), sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
